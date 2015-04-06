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

If no argument is specified `logtype` is set to "syslog". This is destined to be utilized by Logstash in the filter logic.

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
$ModLoad            omprog
$ActionOMProgBinary /path/to/zmqpush.py
*.*                 :omprog:;RSYSLOG_SyslogProtocol23Format
```


Details
=======

`stdin_queuer()` is configured to perform an edge-triggered poll on sys.stdin which will timeout after 50ms. 

If input is detected during the poll, a `for line in sys.stdin` loop is initiated to pull all available input into the queue. If there is a continuous stream of input, `stdin_queuer()` will most likely not leave this loop.

Hence, a `yield` is given after every line from stdin is queued so `zmq_pusher()` can pick up the message and immediately write it to the ZeroMQ socket. This allows continuous streams of input to be shipped via `zmq_pusher()` properly.

If no input is detected by the poll, `stdin_queuer()` simply yields, which allows `zmq_pusher()` to clear the queue, if nescessary, without having to wait for the yield in the  `for line in sys.stdin` loop - which normally is blocking. This allows `zmq_pusher()` to properly drain the socket buffer and/or queue in the event of connectivity issues, regardless of input.

If there is no active input, the application will essentially just poll stdin every 50ms. This setting could be contested based on the reported CPU usage, but it's so computationally minimal as to be irrelevant. It's relatively light on interrupts and context switches as well.

Following tests were performed on a single-core VM.

No load:
```
$ pidstat -C zmqpush.py 1 60 -u -w
..

50ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        25873    0.50    0.02    0.00    0.52     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        25873     19.90      0.20  zmqpush.py

75ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        25931    0.37    0.02    0.00    0.38     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        25931     13.30      0.10  zmqpush.py

100ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        25965    0.28    0.02    0.00    0.30     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        25965      9.98      0.07  zmqpush.py
```

Slight load:
```
$ while true; do seq 1 100 | logger -t test && sleep 1; done &
$ pidstat -C zmqpush.py 1 60 -u -w
..

50ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        27854    2.14    0.64    0.00    2.78     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        27854     25.28     97.36  zmqpush.py

75ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        27515    2.06    0.62    0.00    2.68     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        27515     19.75     97.04  zmqpush.py

100ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        25965    1.99    0.55    0.00    2.54     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        25965     15.21     97.69  zmqpush.py
```

High load:
```
$ while true; do seq 1 100 | logger -t test && sleep 0.2; done &
$ pidstat -C zmqpush.py 1 60 -u -w
..

50ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        27854    9.00    4.53    0.00   13.53     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        27854     44.29    456.13  zmqpush.py

75ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        29581    8.83    4.62    0.00   13.44     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        29581     39.09    456.25  zmqpush.py

100ms:
Average:          PID    %usr %system  %guest    %CPU   CPU  Command
Average:        30805    9.04    4.55    0.00   13.59     -  zmqpush.py
Average:          PID   cswch/s nvcswch/s  Command
Average:        30805     33.75    457.31  zmqpush.py
```
