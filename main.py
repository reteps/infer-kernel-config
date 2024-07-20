import argparse
from vmlinux_to_elf.kallsyms_finder import KallsymsFinder
from vmlinux_to_elf.vmlinuz_decompressor import obtain_raw_kernel_from_file
from vmlinux_to_elf.architecture_detecter import ArchitectureName
import re
import os
from ripgrepy import Ripgrepy
from packaging.version import Version
import subprocess
from pathlib import Path
import git
import shutil
import logging
from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn

# Create a progress bar with custom columns
progress = Progress(
    TextColumn("[progress.description]{task.description:<20}"),
    BarColumn(),
    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    TextColumn("{task.completed}/{task.total}"),
    TimeElapsedColumn(),
    TimeRemainingColumn(),
)

s_progress = Progress(
    TextColumn("[progress.description]{task.description:<20}"),
    BarColumn(),
    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    TextColumn("{task.completed}/{task.total}"),
)
display = Live(Group(progress, s_progress))

console = Console()

def type_to_arch_dir(type: ArchitectureName) -> str:
   return {
    ArchitectureName.mipsle: 'mips',
    ArchitectureName.mipsbe: 'mips',
    ArchitectureName.mips64le: 'mips',
    ArchitectureName.mips64be: 'mips',
    ArchitectureName.x86: 'x86',
    ArchitectureName.x86_64: 'x86',
    ArchitectureName.powerpcbe: 'powerpc',
    ArchitectureName.powerpcle: 'powerpc',
    ArchitectureName.armbe: 'arm',
    ArchitectureName.armle: 'arm',
    ArchitectureName.mips16e: 'mips',
    ArchitectureName.superhle: 'sh',
    ArchitectureName.superhbe: 'sh',
    ArchitectureName.aarch64: 'arm',
    ArchitectureName.sparc: 'sparc',
    ArchitectureName.arcompact: 'arc',
   }[type]

def download_linux_source(linux_version, into_dir):
    linux_branch = 'v' + linux_version
    source_tree = os.path.join(into_dir, linux_version)
    if not os.path.isdir(source_tree):
        console.log(f'Downloading linux version {linux_branch}')
        git.Repo.clone_from('https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git', source_tree, branch=linux_branch, depth=1)
    return source_tree

def download_backports(linux_version, into_dir):
    backports = [
       ('5.15.162', 'v5.15.162-1', '4.4'), # backport 5.15.162 to 4.4+
       ('5.10.168', 'v5.10.168-1', '3.10'), # backport 6.10.168 to 3.10+
       ('4.14-rc2', 'v4.14-rc2-1', '3.0'), #backport 4.14-rc2 to 3.0+
       ('3.14', 'v3.14-1', '2.6.25'), # backport 3.14 to 2.6.25+
    ]

    backport_branch = None
    backport_linux_branch = None
    for (linux_branch, branch, oldest_supported) in backports:
       if Version(linux_version) >= Version(oldest_supported):
          backport_linux_branch, backport_branch = linux_branch, branch
          break

    source_tree = os.path.join(into_dir, f"backports-{backport_branch}")
    if not os.path.isdir(source_tree):
        console.log(f'Downloading backport version {backport_branch}')
        git.Repo.clone_from('https://git.kernel.org/pub/scm/linux/kernel/git/backports/backports.git', source_tree, branch=backport_branch, depth=1)

        '''
        Some may say 'just use python2' -- I say that I would have to install it from source in my Dockerfile, so no.
        '''
        if Version(linux_version) < Version('3.0.0'):
            console.log('Applying python 2->3 fixes')
            subprocess.call(['2to3', '-w', os.path.join(source_tree, 'gentree.py')])
            subprocess.call(['reindent', os.path.join(source_tree, 'gentree.py')])
            subprocess.call(['2to3', '-w', os.path.join(source_tree, 'lib')])

            # Older versions expect --backup-suffix but now it's --suffix
            file = Path(os.path.join(source_tree, 'lib', 'bpcoccinelle.py'))
            file.write_text(file.read_text().replace('--backup-suffix', '--suffix'))

            # Undo the 'python3 future-proofing'
            file = Path(os.path.join(source_tree, 'lib', 'patch.py'))
            file.write_text(file.read_text().replace('__next__ = next', ''))

    return source_tree, backport_linux_branch

if __name__ == '__main__':
    formatter = logging.Formatter(fmt='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    parser = argparse.ArgumentParser()
    parser.add_argument('kernel', help='Path to a kernel')
    args = parser.parse_args()
    data = open(args.kernel, 'rb').read()

    kallsyms = KallsymsFinder(obtain_raw_kernel_from_file(data), None)

    symbols = [x[1:] for x in kallsyms.symbol_names]

    matches = re.search(rb'Linux version (\d+\.[\d.]*\d)[ -~]+', data).groups()
    if len(matches) != 1:
        raise NotImplementedError('case not handled')
    
    full_arch = kallsyms.architecture.name
    linux_version = matches[0].decode('utf-8')
    console.log(f'Symbols: {len(symbols)} Linux: {linux_version}, Arch: {full_arch}')
    linux_src = download_linux_source(linux_version, into_dir=os.path.join('/', 'kernels'))
    linux_target_backport = linux_src + '-backport'

    backport_src, future_linux_version = download_backports(linux_version, into_dir=os.path.join('/', 'backports'))
    future_linux_src = download_linux_source(future_linux_version, into_dir=os.path.join('/', 'kernels'))

    # If we don't think we have been backported yet, generate backported files and merge trees
    if not os.path.isfile(os.path.join(linux_src, 'versions')):
        console.log('Generating and merging backports tree')
        subprocess.call(['python3', 'gentree.py', future_linux_src, linux_target_backport], cwd=backport_src)
        shutil.copytree(linux_target_backport, linux_src, dirs_exist_ok=True)
        shutil.rmtree(linux_target_backport)
    # now merge
    # cp -R 2.6.32.68-backport/* 2.6.32.68

    arch_dir = type_to_arch_dir(kallsyms.architecture)
    task = progress.add_task("Looking for symbols", total=len(symbols))
    success_task = s_progress.add_task("Successes")

    with display:
        for i, symbol in enumerate(symbols):
            patterns = '|'.join(list(map(re.escape, [
            f'EXPORT_SYMBOL({symbol})',
            f'EXPORT_SYMBOL_GPL({symbol})',
            ])) + [
            symbol + r'\(.*\)[\s]*\{', # function declarations
            f'#define {symbol}', # macros
            r'(data\d+|ptr|(\.[a-zA-Z_]*))\W*' + symbol # Assembly declarations
            ])
            
            rg = Ripgrepy(patterns, linux_src)
            # If multiple globs match a file or directory, the glob given later takes precedence.
            cmd = rg.with_filename()\
                .multiline()\
                .ignore_case()\
                .files_with_matches()\
                .no_ignore_dot()\
                .glob('*')\
                .glob('!**/Documentation/*')\
                .glob('!**/arch/*')\
                .glob(f'**/arch/{arch_dir}')
            
            files = cmd.run().as_string
            files = [] if len(files) == 0 else files.split()


            progress.update(task, advance=1)
            s_progress.update(success_task, advance=1 if len(files) > 0 else 0, total=progress.tasks[task].completed)

            progress.log(symbol, files)

    # XXX
    # We must look at the Makefile in the same directory as the file with the 
    # definition in order to take the config option -> use fgrep