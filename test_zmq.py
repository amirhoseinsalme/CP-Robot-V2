import zmq
import time

ctx = zmq.Context()
sub = ctx.socket(zmq.SUB)
sub.setsockopt_string(zmq.SUBSCRIBE, "")
sub.connect("tcp://127.0.0.1:5557")

print("Listening on port 5557... (waiting 30 seconds)")
sub.setsockopt(zmq.RCVTIMEO, 30000)

try:
    msg = sub.recv_string()
    print(f"RECEIVED: {msg[:200]}")
except zmq.error.Again:
    print("TIMEOUT - no message received in 30 seconds")
finally:
    sub.close()
    ctx.term()
