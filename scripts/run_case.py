#!/usr/bin/env python3
"""Bounded POSIX runner for an inspected standalone source. NOT a sandbox."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def reference(path):
    return dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def tree_rss(pid):
    seen, todo, rss = set(), [pid], 0
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        root = Path('/proc') / str(current)
        try:
            for line in (root / 'status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    rss += int(line.split()[1]) * 1024
            for child_file in (root / 'task').glob('*/children'):
                try:
                    todo.extend(int(x) for x in child_file.read_text().split())
                except (OSError, ValueError):
                    pass
        except (OSError, ValueError):
            pass
    return rss


def worker(config_path):
    import resource
    config = json.loads(Path(config_path).read_text())
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (config['cpu'], config['cpu']))
    resource.setrlimit(resource.RLIMIT_AS, (config['address_space'], config['address_space']))
    os.execv(config['command'][0], config['command'])


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--reviewed-local-code', action='store_true')
    parser.add_argument('--wall-seconds', type=float, default=60)
    parser.add_argument('--cpu-seconds', type=int, default=45)
    parser.add_argument('--rss-mb', type=int, default=2048)
    parser.add_argument('--address-space-mb', type=int, default=16384)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.name != 'posix' or not Path('/proc').is_dir():
        parser.error('This helper needs POSIX /proc; use an existing native runner with equivalent guard records')
    if not args.reviewed_local_code:
        parser.error('Inspect code and apply required isolation first; this runner is NOT a sandbox')
    env = json.loads(args.environment.read_text())
    executable = env.get('executable', '')
    if not (env.get('verified') is True and env.get('selection_reason') and
            env.get('library', {}).get('name') and env['library'].get('version') and
            Path(executable).is_absolute() and Path(executable).is_file() and os.access(executable, os.X_OK)):
        parser.error('A verified existing environment, absolute executable, library version and selection reason are required; ask the operator if unavailable')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or command[0] != executable or not any('{source}' in x for x in command):
        parser.error('Use the exact selected executable and a {source} argument')
    if not all(math.isfinite(x) and x > 0 for x in [args.wall_seconds, args.cpu_seconds, args.rss_mb, args.address_space_mb]):
        parser.error('Execution budgets must be finite positive numbers')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / ('source' + args.source.suffix)
    source.write_bytes(args.source.read_bytes())
    command = [x.replace('{source}', str(source)) for x in command]
    config = dict(command=command, cpu=args.cpu_seconds, address_space=args.address_space_mb * 1024**2)
    (out / 'worker.json').write_text(json.dumps(config))
    started = datetime.now(timezone.utc).isoformat()
    begin, peak, guard = time.monotonic(), 0, None
    with (out / 'stdout.txt').open('x') as stdout, (out / 'stderr.txt').open('x') as stderr:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(out / 'worker.json')],
                                cwd=out, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while proc.poll() is None:
                peak = max(peak, tree_rss(proc.pid))
                if time.monotonic() - begin >= args.wall_seconds:
                    guard = 'timeout'
                elif peak > args.rss_mb * 1024**2:
                    guard = 'memory_limit'
                if guard:
                    break
                time.sleep(0.025)
        finally:
            # Also terminate children left after a successful parent exit.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    code = proc.returncode
    status = guard or ('process_signal' if code < 0 else 'returned' if code == 0 else 'nonzero_exit')
    result = dict(execution_performed=True, execution_status=status, returncode=code,
                  started_at=started, elapsed_seconds=round(time.monotonic()-begin, 3),
                  command=command, cwd=str(out), environment=env,
                  source=reference(source), stdout=reference(out/'stdout.txt'), stderr=reference(out/'stderr.txt'),
                  limits=dict(wall_seconds=args.wall_seconds, cpu_seconds=args.cpu_seconds,
                              tree_rss_bytes=args.rss_mb*1024**2, address_space_bytes=args.address_space_mb*1024**2,
                              core_bytes=0), peak_tree_rss_bytes=peak,
                  isolation='POSIX process group and resource limits; no filesystem/network sandbox',
                  symptom_assessment='not_performed_by_runner')
    temporary = out / 'result.json.tmp'
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    temporary.rename(out/'result.json')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
