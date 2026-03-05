import sys
import os

# This adds the root directory to the path so 'custom_components' can be found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))