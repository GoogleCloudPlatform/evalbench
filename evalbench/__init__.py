import os
import sys

# Expose internal subdirectories to sys.path so legacy absolute imports
# (e.g. 'from reporting.report import ...') resolve correctly when run globally.
sys.path.insert(0, os.path.dirname(__file__))


from . import reporting
from . import util
from . import dataset
from . import evaluator
