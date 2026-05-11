import os
import sys

# Append package root to sys.path for legacy absolute imports.
# Using append (rather than insert) prevents namespace collisions in spawned child processes.
sys.path.append(os.path.dirname(__file__))


from . import reporting
from . import util
from . import dataset
from . import evaluator
