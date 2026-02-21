
import socket
import sys

def check_port(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    result = sock.connect_ex((host, port))
    sock.close()
    return result == 0

if __name__ == "__main__":
    port = 18792
    print(f"Checking port {port}...")
    if check_port("127.0.0.1", port):
        print(f"✅ OpenClaw Port {port} is OPEN (Listening)")
    else:
        print(f"❌ OpenClaw Port {port} is CLOSED (Not Listening)")
        print("   -> Browser Extension is likely NOT connected.")
