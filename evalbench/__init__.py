import os
import sys

# Inject package root into sys.path at import time.
# This allows legacy absolute imports (e.g. 'from reporting.report import ...')
# to resolve correctly when evalbench is installed as a packaged global CLI tool.
sys.path.insert(0, os.path.dirname(__file__))


from . import reporting
from . import util
from . import dataset
from . import evaluator
