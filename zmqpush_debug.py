#!/usr/bin/env python3
"""
https://github.comcast.com/gpacui001c/zmqpush
v0.5
"""

import asyncio
import fcntl
import os
import select, sys
#import socket
import zmq, aiozmq

# Caused by building aiozmq against newer zmq sources
# Shouldn't happen with the current zmq/aiozmq RPM packages
#try:
#    import aiozmq
#except AttributeError:
#    zmq.STREAM = zmq.STREAMER
#    import aiozmq

## Configuration variables
loghost = "10.0.0.1" #logstash intake
logport = "5014"
##

## Internal variables
pid = os.getpid()
# ZeroMQ connection doesn't do hostname lookup
#loghost     = socket.gethostbyname(loghost)
# Initiate msgcount counter
msgcount = 0
##

def escape(s):
    """Backslash-escape string for proper logstash parsing"""

    # note, this is changing \ -> \\ and " -> \" respectively
    return s.replace('\\', '\\\\').replace('"', '\\"')

# Decorators: https://www.python.org/dev/peps/pep-0318/
@asyncio.coroutine
def zmq_pusher(q, loop, zmq_future, stdin_future):
    """
    Utilizes shared queue object (q) to get messages,
    which are then written to a ZeroMQ PUSH socket.

    ZMQFuture object is updated once socket has been stood up.
    """

    global msgcount # pylint: disable=global-statement

    try:
        logf.write(b'before-pusher\n')
        # Stand up ZeroMQ socket
        # (this does not initiate any network connection)
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
        low = pusher.transport.get_write_buffer_limits()[0]

        # Inform stdin_queuer() to continue
        zmq_future.set_result(True)

        while True:

            if pusher.transport.get_write_buffer_size() > low:
                # Socket buffer has hit the low watermark threshold,
                # any further processing from queue is prevented until buffer clears.
                logf.write(b'above-low-write\n')
                logf.write(b'before-drain-buffer\n')
                # Attempt to drain buffer every 100ms
                # stdin_queuer() will continue to fill the queue while this yields
                yield from pusher.drain()
                yield from asyncio.sleep(0.1)
                logf.write(b'after-drain-buffer\n')

            else:
                # Socket buffer is within threshold

                if stdin_future.cancelled() and q.empty():
                    # stdin_queuer() finally clause will cancel STDINFuture,
                    # If that is cancelled and queue is empty, we're done.
                    logf.write(b'EOF\n')
                    break

                ## Get message from queue
                # `yield from` allows other coroutines to run until something is returned
                logf.write(b'before-q-get\n')
                line = yield from q.get()

                ## Format the message
                jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (escape(line),
                                                                      logtype,
                                                                      pid)
                ## Write message to the ZeroMQ socket
                # This is a non-blocking call, and therefor does not guarantee delivery
                # write() takes a bytes-tuple (topic, message), no topic needed for PUSH socket
                pusher.write((b'', jsonmsg.encode('utf-8')))
                logf.write(b'after-q-get\n')

                # Whether or not message is delivered via the socket (we can't know),
                # increment msgcount variable
                msgcount += 1
                if q.empty():
                    yield

        loop.stop()

    except RuntimeError:
        # Loop was stopped by stdin_queuer()
        pass

@asyncio.coroutine
def stdin_queuer(q, loop, zmq_future, stdin_future):
    """
    Utilizes shared queue object (q) to put messages in.

    STDINFuture cancellation indicates EOF to zmq_pusher()
    """

    try:
        # Wait max. 1s for ZeroMQ connection to be stood up
        # (Seems to prevent race condition at start up)
        yield from asyncio.wait_for(zmq_future, 1, loop=loop)
        logf.write(b'after-pusher\n')

        while True:
            logf.write(b'inside-queuer-loop\n')
            # Wait 50ms for input before yielding
            # If zmq_pusher() is idle, this will poll continuously.
            poll = poller.poll(timeout=0.05)
            logf.write(b'after-poll\n')
            # Pipe input event comes in as EPOLLIN or EPOLLIN | EPOLLHUP (1 | 16 = 17)
            # Safe to assume just EPOLLHUP means EOF
            # poller returns [(fd, event)], hence poll[0][1] == event
            logf.write(str(poll).encode())
            if poll and poll[0][1] != select.EPOLLHUP:
                logf.write(b'if-poll\n')
                # Input detected, and not EOF
                for line in sys.stdin:
                    logf.write(b'for-line-stdin\n')
                    #logf.write(line.encode())
                    # Consume all available input and place into queue
                    yield from q.put(line.strip())
                    logf.write(b'after-q-put\n')
                    yield
                logf.write(b'if-poll-before-continue\n')
                continue

            elif not poll:
                # No input detected, still no EOF either
                logf.write(b'if-not-poll\n')
                yield
                continue

            else:
                logf.write(b'HUP-break\n')
                # EOF
                break
            logf.write(b'bad-beef\n') #should not happen

    except: # pylint: disable=bare-except
        logf.write(b'exception\n')
        e = sys.exc_info()[0]
        logf.write(str(e).encode())
        logf.write(b'\n')
        # Practically, this stops the application
        _zmq_pusher_task.cancel()
        loop.stop()

    finally:
        logf.write(b'queuer-finally\n')
        # zmq_pusher() will use this as a EOF notification
        stdin_future.cancel()

if __name__ == '__main__':

    # Setup logtype
    if len(sys.argv) > 1:
        logtype = sys.argv[1]
    else:
        logtype = "syslog"

    # Initiate asyncio objects
    _q = asyncio.Queue()
    _loop = asyncio.get_event_loop()
    _ZMQFuture = asyncio.Future()
    _STDINFuture = asyncio.Future()

    _zmq_pusher_task = asyncio.async(zmq_pusher(_q, _loop, _ZMQFuture, _STDINFuture))
    _stdin_queuer_task = asyncio.async(stdin_queuer(_q, _loop, _ZMQFuture, _STDINFuture))

    try:
        ## Unbuffered debug log
        logf = open('/tmp/zmqpush', 'w+b', buffering=0)
        # Set sys.stdin non-blocking
        fcntl.fcntl(sys.stdin, fcntl.F_SETFL, os.O_NONBLOCK)
        # Edge-Triggered epoll (for non-blocking file descriptors)
        poller = select.epoll()
        poller.register(sys.stdin, select.EPOLLIN | select.EPOLLET)

        _start = _loop.time()
        _loop.run_forever()

    finally:
        _end = _loop.time()

        print('Processed %d messages in %.04fms. Tagged with @pid:%d' %
              (msgcount, (_end-_start)*1000, pid))
