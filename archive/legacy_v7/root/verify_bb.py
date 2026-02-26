
from src.utils.black_box import BlackBox
import os

bb = BlackBox("tests/test_black_box.db")
bb.log_message(123, "TestChat", 456, "Tester", "testuser", "INCOMING", "Hello Krab")
stats = bb.get_stats()
print(f"Stats: {stats}")

if stats['total'] == 1:
    print("✅ Black Box working!")
else:
    print("❌ Black Box failed!")

os.remove("tests/test_black_box.db")
