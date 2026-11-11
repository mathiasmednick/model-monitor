"""Compatibility entry point for the Model Monitor verification suite."""
from test_hub import *
from test_chat import ChatTests
from test_help import HelpTests
from test_gpu import GpuTests
from test_repair import RepairTests
from test_optimize import OptimizeTests
from test_terminal import TerminalTests
from test_allocation import AllocationTests
from test_restart import RestartTests
from test_projects import ProjectTests

if __name__ == '__main__':
    unittest.main(verbosity=2)
