#!/usr/bin/env python3
"""Run hermetic suites without starting LiteLLM, contacting services, or using user state."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'harness'))


def main():
    import pytest
    with tempfile.TemporaryDirectory(prefix='.offline-tests-', dir=ROOT) as scratch:
        old = tempfile.tempdir
        tempfile.tempdir = scratch
        try:
            return pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp', str(Path(scratch) / 'pytest'),
                'gate/test_route_change.py', 'catalog/test_infra_validate.py',
                'harness/test_trigger_matrix_plan.py', 'tests/test_offline.py'])
        finally:
            tempfile.tempdir = old


if __name__ == '__main__':
    raise SystemExit(main())
