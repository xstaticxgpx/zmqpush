#!/usr/local/bin/python3
import asyncio
import fcntl
import os
import select, socket, sys
import zmq

try:
    import aiozmq
except AttributeError:
    zmq.STREAM = zmq.STREAMER
    import aiozmq

# config
loghost = "localhost"
logport = "5014"

# internal variables
pid         = os.getpid()
loghost     = socket.gethostbyname(loghost)
msgcount    = 0

# Pipe input comes in as EPOLLIN | EPOLLHUP (1 | 16 = 17)
# Safe to assume just EPOLLHUP means EOF
__poll_eof  = [(0, select.EPOLLHUP)]
# Ctrl+D on the command line doesn't seem to initiate any EOF,
# so we catch Ctrl+C via KeyboardInterrupt exception

def quote_escape(s):
    #return s.replace('"', '\\"')
    return s.replace('"', "'")

@asyncio.coroutine
def zmq_pusher(q, loop, ZMQFuture, STDINFuture):
    global msgcount
    global loghost, logport
    
    try:
        #logf.write(b'before-pusher\n')

        # Stand up ZeroMQ socket
        socket = zmq.Context.instance().socket(zmq.PUSH)

        # 1s connection retry interval
        # (needs to be set before connection is initiated)
        # default: 100
        socket.setsockopt(zmq.RECONNECT_IVL, 1000)

        pusher = yield from aiozmq.create_zmq_stream(
                            zmq_type=zmq.PUSH, zmq_sock=socket,
                            connect="tcp://%s:%d" % (loghost, int(logport)))

        # 0s timeout to prevent hang on trying to complete socket
        # (will drop msgs if EOF is reached and connection is down)
        # default: -1 (infinite)
        pusher.transport.setsockopt(zmq.LINGER, 0)
        low, high = pusher.transport.get_write_buffer_limits()
    
        # Inform stdin_queuer to continue
        ZMQFuture.set_result(True)

        while True:

            if pusher.transport.get_write_buffer_size() > low:
                #logf.write(b'above-low-write\n')
                #logf.write(b'before-drain-buffer\n')
                yield from pusher.drain()
                yield from asyncio.sleep(0.1)
                #logf.write(b'after-drain-buffer\n')

            else:
                if STDINFuture.cancelled() and q.empty():
                    #logf.write(b'EOF\n')
                    break
                #logf.write(b'before-q-get\n')
                # Get pre-.strip()'d line from queue
                line = yield from q.get()
                # JSON format the message
                jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (quote_escape(line),
                                                                      logtype, 
                                                                      pid)
                # Write message to the ZeroMQ socket
                pusher.write((b'', jsonmsg.encode('utf-8')))
                #logf.write(b'after-q-get\n')
                msgcount += 1
                if q.empty():
                    yield
        # don't do this.. will lose messages
        #pusher.close()

    finally:
        loop.stop()

@asyncio.coroutine
def stdin_queuer(q, loop, ZMQFuture, STDINFuture):
    global poller, __poll_eof
    global pid
    global logtype

    try:
        # Wait max. 1s for ZeroMQ connection to be stood up
        # (prevents race condition at start up)
        yield from asyncio.wait_for(ZMQFuture, 1, loop=loop)
        #logf.write(b'after-pusher\n')

        while True:
            
            # Check for input every 50ms
            poll = poller.poll(timeout=0.05)
            if poll and poll != __poll_eof:

                for line in sys.stdin:
                    yield from q.put(line.strip())
                    #logf.write(b'after-q-put\n')
                    yield

            elif not poll:
                yield

            else:
                # EOF
                break

    except (KeyboardInterrupt, InterruptedError):
        # Ctrl+C or Ctrl+Z
        loop.stop()

    finally:
        STDINFuture.cancel()

if __name__ == '__main__':

    if len(sys.argv) > 1:
        logtype = sys.argv[1]
    else:
        logtype = "syslog"

    q = asyncio.Queue()
    loop = asyncio.get_event_loop()
    ZMQFuture = asyncio.Future()
    STDINFuture = asyncio.Future()

    asyncio.async(zmq_pusher(q, loop, ZMQFuture, STDINFuture))
    asyncio.async(stdin_queuer(q, loop, ZMQFuture, STDINFuture))

    try:
        #logf = open('/tmp/zmqpush', 'w+b', buffering=0)

        # Set sys.stdin non-blocking
        fcntl.fcntl(sys.stdin, fcntl.F_SETFL, os.O_NONBLOCK)
        # Edge-Triggered epoll (for non-blocking file descriptors)
        poller = select.epoll()
        poller.register(sys.stdin, select.EPOLLIN | select.EPOLLET)
        
        _start = loop.time()
        loop.run_forever()

    finally:
        _end = loop.time()
    
        print('Processed %d messages in %.04fms. Tagged with @pid:%d' % (msgcount, (_end-_start)*1000, pid))
        sys.exit(0)
