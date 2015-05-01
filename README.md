zmqpush
=======

Asynchronously queue STDIN and push across the wire using a ZeroMQ socket.

Built with the new (in python3.4) asyncio module and aiozmq.

Utilizes NONBLOCK-ing stdin and Edge-Triggered epoll() to detect content in stdin.

Designed to recieve input via a unix pipe and send to Logstash zeromq input.

Currently, it formats every line from stdin into the following JSON:

```
jsonmsg = '{"message":"%s","type":"%s","@pid":%d}' % (quote_escape(line),
                                                                   logtype,
                                                                   pid)
```

`zmqpush` will take the 1st command-line argument and assign it to `logtype`. 

If no argument is specified, `logtype` is set to "syslog" in order to compensate for rsyslog OMProg's inability to pass arguments.

`logtype` variable is put into the JSON as "type", which allows it to be automatically parsed and utilized directly by Logstash.

`@pid` is the currently executing zmqpush process ID, and was added mostly for debugging, however it remains since it may be useful down the line.

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

`zmq_pusher()` stands up the ZeroMQ socket, updates ZMQFuture object, then enters a `while True` in which it perpetually writes messages from the queue to the ZeroMQ socket.

`stdin_queuer()` will wait for the ZMQFuture object, then enter a `while True` loop in which it perpetually performs a poll for input on stdin, which it places into the queue.

If input is detected during the poll cycle, the inner `for line in sys.stdin` loop is initiated to pull all available input into the queue. If there is a continuous stream of input, `stdin_queuer()` will most likely not leave the inner for loop, therefor a `yield` is given after every line from stdin is queued so `zmq_pusher()` can pick up the message and immediately write it to the ZeroMQ socket. This allows continuous streams of input to be shipped via `zmq_pusher()` in a practically synchronous manner.

If no input is detected, `stdin_queuer()` just yields, which allows `zmq_pusher()` the chance to clear the socket buffer and/or queue, if nescessary. Most likely, if there's no connectivity issues, `zmq_pusher()` will have nothing to do, so it instantly comes back to `stdin_queuer()` and the poll cycle starts again, and again, etc.

50ms for the poll cycle (timeout) could be unnescessarily frequent, however further down the stats show its barely utilizing any resources at all - and having it this low allows it to be responsive to new input and quick in its recovery after connectivity issues.

If there are connectivity issues, `zmq_pusher()` has logic to catch when the socket buffer has exceeded the low watermark, which will stop it from writing any further messages from the queue to the socket. Instead it will initiate an asynchronous buffer drain on the socket, then sleep for 100ms.
Meanwhile, `stdin_queuer()` will continue to read and put input into the queue, which inevitably would increase the memory footprint. Depending on the amount of input, and how long connectivity is down, the memory footprint could grow minimally or massively - which may be a possible hazard in some extreme situation. Queue size limit could be configured so there's some sort of ceiling, however it is currently unlimited.

The buffer watermarks are configurable, however by default ZMQ automatically sets them based within the OS socket settings, which is probably ideal. Since we're using a PUSH socket, let's compare against TCP write memory:
```
# ./zmqpush-watermark.py 
Low watermark: 16384
High watermark: 65536

# sysctl -a | grep 'tcp_wmem'
net.ipv4.tcp_wmem = 4096	16384	4194304
```


Stats
=======

Following tests were performed on a single-core VM, with zmqpush running via rsyslog, comparing various poll timeouts-

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
