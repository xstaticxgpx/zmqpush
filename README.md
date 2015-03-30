zmqpush
=======

Asynchronously queue STDIN and push with ZeroMQ.

Designed to work via a unix pipe.


Examples
=======

Shell pipes:
```
$ echo test | ./zmqpush.py 
Processed 1 messages in 2.2003ms. Tagged with @pid:19867

$ seq 1 100 | ./zmqpush.py 
Processed 100 messages in 37.0543ms. Tagged with @pid:19863

$ for x in {1..3}; do seq 1 100 | ./zmqpush.py  && sleep 1; done
Processed 100 messages in 52.6803ms. Tagged with @pid:19848
Processed 100 messages in 48.8532ms. Tagged with @pid:19853
Processed 100 messages in 50.1794ms. Tagged with @pid:19858
```

rsyslog5: Ship all messages with RFC5424 format
```
$ cat /etc/rsyslog.d/01-shipper.conf 
$ModLoad omprog
$ActionOMProgBinary /home/gpacui001c/Build/zmqpush.py
*.*							:omprog:;RSYSLOG_SyslogProtocol23Format
```
