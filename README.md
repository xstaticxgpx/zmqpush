zmqpush
=======

Asynchronously queue STDIN and push across the wire using a ZeroMQ socket.

Built with the new (in python3.4) asyncio module and aiozmq.

Utilizes NONBLOCK-ing stdin and Edge-Triggered epoll() to detect content in stdin.

Designed to work via a unix pipe.

Currently, it formats messages in the following JSON format:

```
jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (quote_escape(line),
                                                                   logtype,
                                                                   pid)
```

`zmqpush` will take the 1st command-line argument and assign it to `logtype`. 

If no arguments are specified `logtype` is set to "syslog". This is destined to be utilized by Logstash in the filter logic.

Examples
=======

Shell pipes:
```
$ echo test | zmqpush.py
Processed 1 messages in 2.2003ms. Tagged with @pid:19867

$ seq 1 100 | zmqpush.py
Processed 100 messages in 37.0543ms. Tagged with @pid:19863

$ for x in {1..3}; do seq 1 100 | zmqpush.py && sleep 1; done
Processed 100 messages in 52.6803ms. Tagged with @pid:19848
Processed 100 messages in 48.8532ms. Tagged with @pid:19853
Processed 100 messages in 50.1794ms. Tagged with @pid:19858

$ while true; do seq 1 100 && sleep 1; done | zmqpush.py 
^CProcessed 300 messages in 2460.1966ms. Tagged with @pid:17301
```

rsyslog5: Ship all messages with RFC5424 format
```
$ModLoad omprog
$ActionOMProgBinary /path/to/zmqpush.py
*.*							:omprog:;RSYSLOG_SyslogProtocol23Format
```


Details
=======

`stdin_queuer()` is configured to perform an edge-triggered epoll on sys.stdin which will timeout after 50ms. When input is detected, a `for line in sys.stdin` loop is initiated to pull all available input into the queue.

A `yield` is given after every line from stdin is queued so `zmq_pusher()` can pick up the message out of the queue and instantly write it to the ZeroMQ socket. This allows continuous streams of input to be worked on asynchronously.

If no input is detected by the epoll, `stdin_queuer()` simply yields, which allows `zmq_pusher()` to clear the queue, if nescessary, without needing to wait for the yield in the  `for line in sys.stdin` loop - which normally is blocking. This allows `zmq_pusher()` to properly drain the socket buffer and internal queue in the event of connectivity issues, regardless of input.
