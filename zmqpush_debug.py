#!/usr/local/bin/python3
import asyncio
import os
import socket, sys
import zmq

try:
    import aiozmq
except AttributeError:
    zmq.STREAM = zmq.STREAMER
    import aiozmq

# non-blocking stdin/out
#import fcntl
#fcntl.fcntl(sys.stdin, fcntl.F_SETFL, os.O_NONBLOCK)
#fcntl.fcntl(sys.stdout, fcntl.F_SETFL, os.O_NONBLOCK)

# config
loghost = "localhost"
logport = "5014"

# internal variables
pid = os.getpid()
loghost = socket.gethostbyname(loghost)
msgcount = 0

def quote_escape(s):
    return s.replace('"', '\\"')

@asyncio.coroutine
def zmq_pusher(q, loop, EOFFuture, ZMQFuture):
    global logf
    global msgcount
    global loghost, logport
    
    try:
        logf.write(b'before-pusher\n')

        socket = zmq.Context.instance().socket(zmq.PUSH)
        # 1s connection retry interval
        # default: 100
        socket.setsockopt(zmq.RECONNECT_IVL, 1000)

        pusher = yield from aiozmq.create_zmq_stream(
                            zmq.PUSH,
                            zmq_sock=socket,
                            connect="tcp://%s:%d" % (loghost, int(logport)))

        # 0s timeout to prevent hang trying to complete socket
        # (will drop msgs if EOF is reached and connection is down)
        # default: -1 (infinite)
        pusher.transport.setsockopt(zmq.LINGER, 0)
        low, high = pusher.transport.get_write_buffer_limits()
        print(low, high)
    
        ZMQFuture.set_result(True)
        while True:
            if pusher.transport.get_write_buffer_size() > low:
                logf.write(b'before-drain\n')
                yield from pusher.drain()
                yield from asyncio.sleep(1)
                logf.write(b'after-drain\n')
            else:
                if EOFFuture.cancelled() and q.empty():
                    logf.write(b'EOF\n')
                    break
                logf.write(b'before-q-get\n')
                line = yield from q.get()
                logf.write(b'after-q-get\n')
                pusher.write((b'', line.encode('utf-8')))
                msgcount += 1
                if q.empty():
                    yield
        logf.write(b'before-final-drain\n')
        yield from pusher.drain()
        logf.write(b'after-final-drain\n')

    except KeyboardInterrupt:
        pass

    finally:
        loop.stop()


@asyncio.coroutine
def stdin_queuer(q, loop, EOFFuture, ZMQFuture):
    global pid
    global logf
    global logtype

    try:
        yield from asyncio.wait_for(ZMQFuture, 1, loop=loop)

        for line in sys.stdin:

            jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (quote_escape(line.strip()),
                                                                  logtype, 
                                                                  pid)
            
            yield from q.put(jsonmsg)
            logf.write(b'after-q-put\n')
            yield

    except KeyboardInterrupt:
        pass
    
    finally:
        EOFFuture.cancel()

if __name__ == '__main__':

    if len(sys.argv) > 1:
        logtype = sys.argv[1]
    else:
        logtype = "syslog"

    q = asyncio.Queue()
    loop = asyncio.get_event_loop()
    EOFFuture = asyncio.Future()
    ZMQFuture = asyncio.Future()

    asyncio.async(zmq_pusher(q, loop, EOFFuture, ZMQFuture))
    asyncio.async(stdin_queuer(q, loop, EOFFuture, ZMQFuture))

    try:
        logf = open('/tmp/zmqpush', 'w+b', buffering=0)
        _start = loop.time()
        loop.run_forever()

    finally:
        _end = loop.time()
    
        print('Processed %d messages in %.04fms. Tagged with @pid:%d' % (msgcount, (_end-_start)*1000, pid))
        sys.exit(0)
