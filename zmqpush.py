#!/usr/bin/env python3
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

## Configuration variables
loghost = "xfwlgs-as-vip.sys.comcast.net"
logport = "5014"
##

## Internal variables
pid         = os.getpid()
# ZeroMQ connection doesn't do host lookup
loghost     = socket.gethostbyname(loghost)
# Initiate msgcount counter
msgcount    = 0
##

def quote_escape(s):
    """Either backslash-escape or replace double quotes"""

    global logtype

    if not "json" in logtype:
        return s.replace('"', "'")
    else:
        return s.replace('"', '\\"')

# Decorators: https://www.python.org/dev/peps/pep-0318/
@asyncio.coroutine
def zmq_pusher(q, loop, ZMQFuture, STDINFuture):
    """
    Utilizes shared queue object (q) to get messages,
    which are then written to a ZeroMQ PUSH socket.

    ZMQFuture object is updated once socket has been stood up.

    STDINFuture cancellation by stdin_queuer() indicates EOF
    """

    #global logf
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
        #pusher.transport.setsockopt(zmq.LINGER, 0)
        low, high = pusher.transport.get_write_buffer_limits()
    
        # Inform stdin_queuer() to continue
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
                    # stdin_queuer() finally clause will cancel STDINFuture,
                    # If that is cancelled and queue is empty, we're done.
                    #logf.write(b'EOF\n')
                    break
                #logf.write(b'before-q-get\n')
                # Get pre-stripped line from queue
                # This is a "blocking" call, but only for this coroutine
                # "yield from" will allow other coroutines to continue
                # this coroutine will progress once the queue returns an item
                line = yield from q.get()
                # JSON format the message
                jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (quote_escape(line),
                                                                      logtype, 
                                                                      pid)
                # Write message to the ZeroMQ socket
                # This does not ensure delivery, and is non-blocking.
                # we have pseudo-blocking logic above to prevent socket buffer overruns
                # Tuple (topic, message) - no topic needed for pushpull topology
                pusher.write((b'', jsonmsg.encode('utf-8')))
                #logf.write(b'after-q-get\n')

                # Whether or not message is delivered via the socket (we can't know),
                # still increment counter variable for sanity
                msgcount += 1
                if q.empty():
                    yield

    finally:
        # Practically, this stops the application
        loop.stop()

@asyncio.coroutine
def stdin_queuer(q, loop, ZMQFuture, STDINFuture):
    global poller
    global pid
    #global logf
    global logtype

    try:
        # Wait max. 1s for ZeroMQ connection to be stood up
        # (Seems to prevent race condition at start up)
        yield from asyncio.wait_for(ZMQFuture, 1, loop=loop)
        #logf.write(b'after-pusher\n')

        while True:
            # Wait 50ms for input before yielding
            # If zmq_pusher() is idle, this will poll continuously.
            # poller returns [(fd, event)], hence poll[0][1] == event
            poll = poller.poll(timeout=0.05)
            # Pipe input event comes in as EPOLLIN | EPOLLHUP (1 | 16 = 17)
            # Safe to assume just EPOLLHUP means EOF
            # Ctrl+D on the command line doesn't seem to initiate any EOF,
            # so we catch Ctrl+C via KeyboardInterrupt exception
            if poll and poll[0][1] != select.EPOLLHUP:
                # Input detected, and not EOF
                for line in sys.stdin:
                    # Consume all available input and place into queue
                    yield from q.put(line.strip())
                    #logf.write(b'after-q-put\n')
                    yield

            elif not poll:
                # No input detected, still no EOF either
                yield

            else:
                # EOF
                break

    except (KeyboardInterrupt, InterruptedError):
        # Ctrl+C or Ctrl+Z
        # Practically, this stops the application
        loop.stop()

    finally:
        # zmq_pusher() will use this as a EOF notification
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
