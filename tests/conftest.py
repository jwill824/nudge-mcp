import sys
import os

# Ensure lib/ (vendored dependencies) is on the path for all tests
_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
sys.path.insert(0, _ROOT)
