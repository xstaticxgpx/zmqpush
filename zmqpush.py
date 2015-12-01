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
loghost = "10.54.140.14" #xfwlgs-as-vip.sys.comcast.net
logport = "5014"
##

## Internal variables
pid = os.getpid()
# ZeroMQ connection doesn't do hostname lookup
#loghost     = socket.gethostbyname(loghost)
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
                # Attempt to drain buffer every 100ms
                # stdin_queuer() will continue to fill the queue while this yields
                yield from pusher.drain()
                yield from asyncio.sleep(0.1)

            else:
                # Socket buffer is within threshold

                if stdin_future.cancelled() and q.empty():
                    # stdin_queuer() finally clause will cancel STDINFuture,
                    # If that is cancelled and queue is empty, we're done.
                    break

                ## Get message from queue
                # `yield from` allows other coroutines to run until something is returned
                line = yield from q.get()

                ## Format the message
                jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (escape(line),
                                                                      logtype,
                                                                      pid)
                ## Write message to the ZeroMQ socket
                # This is a non-blocking call, and therefor does not guarantee delivery
                # write() takes a bytes-tuple (topic, message), no topic needed for PUSH socket
                pusher.write((b'', jsonmsg.encode('utf-8')))

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

        while True:
            # Wait 50ms for input before yielding
            # If zmq_pusher() is idle, this will poll continuously.
            poll = poller.poll(timeout=0.05)
            # Pipe input event comes in as EPOLLIN or EPOLLIN | EPOLLHUP (1 | 16 = 17)
            # Safe to assume just EPOLLHUP means EOF
            # poller returns [(fd, event)], hence poll[0][1] == event
            if poll and poll[0][1] != select.EPOLLHUP:
                # Input detected, and not EOF
                for line in sys.stdin:
                    # Consume all available input and place into queue
                    yield from q.put(line.strip())
                    yield
                continue

            elif not poll:
                # No input detected, still no EOF either
                yield
                continue

            else:
                # EOF
                break
    except: # pylint: disable=bare-except
        # Practically, this stops the application
        _zmq_pusher_task.cancel()
        loop.stop()

    finally:
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
        # Initiate msgcount counter
        msgcount = 0

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
