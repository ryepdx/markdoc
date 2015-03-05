# -*- coding: utf-8 -*-

import codecs
from functools import wraps
import logging
import fnmatch
import os
import os.path as p
import pprint
import re
import shutil
import subprocess
import sys

import markdoc
from markdoc.builder import Builder
from markdoc.cli.parser import subparsers


def command(function):
    """Decorator/wrapper to declare a function as a Markdoc CLI task."""

    cmd_name = function.__name__.replace('_', '-')
    help = (function.__doc__ or '').rstrip('.') or None
    parser = subparsers.add_parser(cmd_name, help=help)

    @wraps(function)
    def wrapper(config, *args, **kwargs):
        logging.getLogger('markdoc').debug('Running markdoc.%s' % cmd_name)
        return function(config, *args, **kwargs)

    wrapper.parser = parser

    return wrapper


# # Utilities

@command
def show_config(config, args):
    """Pretty-print the current Markdoc configuration."""

    pprint.pprint(config)


@command
def init(_, args):
    """Initialize a new Markdoc repository."""

    log = logging.getLogger('markdoc.init')

    if not args.destination:
        log.info('No destination specified; using current directory')
        destination = os.getcwd()
    else:
        destination = p.abspath(args.destination)

    if p.exists(destination) and os.listdir(destination):
        init.parser.error("destination isn't empty")
    elif not p.exists(destination):
        log.debug('makedirs %s' % destination)
        os.makedirs(destination)
    elif not p.isdir(destination):
        init.parser.error("destination isn't a directory")

    log.debug('mkdir %s/.templates/' % destination)
    os.makedirs(p.join(destination, '.templates'))
    log.debug('mkdir %s/static/' % destination)
    os.makedirs(p.join(destination, 'static'))
    log.debug('mkdir %s/wiki/' % destination)
    os.makedirs(p.join(destination, 'wiki'))

    log.debug('Creating markdoc.yaml file')
    config_filename = p.join(destination, 'markdoc.yaml')
    fp = open(config_filename, 'w')
    try:
        fp.write('{}\n')
    finally:
        fp.close()

    if args.vcs_ignore:
        config = markdoc.config.Config.for_directory(destination)
        args = vcs_ignore.parser.parse_args([args.vcs_ignore])
        vcs_ignore(config, args)

    log.info('Wiki initialization complete')
    log.info('Your new wiki is at: %s' % destination)

init.parser.add_argument('destination', default=None,
                         help="Create wiki here (if omitted, defaults to current directory)")
init.parser.add_argument('--vcs-ignore', choices=['hg', 'git', 'cvs', 'bzr'],
                         help="Create an ignore file for the specified VCS.")


@command
def vcs_ignore(config, args):
    """Create a VCS ignore file for a wiki."""

    log = logging.getLogger('markdoc.vcs-ignore')
    log.debug('Creating ignore file for %s' % args.vcs)
    wiki_root = config['meta.root']  # shorter local alias.

    ignore_file_lines = []
    ignore_file_lines.append(p.relpath(config.html_dir, start=wiki_root))
    ignore_file_lines.append(p.relpath(config.temp_dir, start=wiki_root))
    if args.vcs == 'hg':
        ignore_file_lines.insert(0, 'syntax: glob')
        ignore_file_lines.insert(1, '')

    if args.output == '-':
        log.debug('Writing ignore file to stdout')
        fp = sys.stdout
    else:
        if not args.output:
            filename = p.join(wiki_root, '.%signore' % args.vcs)
        else:
            filename = p.join(wiki_root, args.output)
        log.info('Writing ignore file to %s' % p.relpath(filename, start=wiki_root))
        fp = open(filename, 'w')

    try:
        fp.write('\n'.join(ignore_file_lines) + '\n')
    finally:
        if fp is not sys.stdout:
            fp.close()

    log.debug('Ignore file written.')

vcs_ignore.parser.add_argument('vcs', default='hg', nargs='?',
                               choices=['hg', 'git', 'cvs', 'bzr'],
                               help="Create ignore file for specified VCS (default 'hg')")
vcs_ignore.parser.add_argument('-o', '--output', default=None, metavar='FILENAME',
                               help="Write output to the specified filename, relative to the wiki root. "
                                    "Default is to generate the filename from the VCS. "
                                    "'-' will write to stdout.")


## Cleanup

@command
def clean_html(config, args):
    """Clean built HTML from the HTML root."""

    log = logging.getLogger('markdoc.clean-html')

    if p.exists(config.html_dir):
        log.debug('rm -Rf %s' % config.html_dir)
        shutil.rmtree(config.html_dir)

    log.debug('makedirs %s' % config.html_dir)
    os.makedirs(config.html_dir)


@command
def clean_temp(config, args):
    """Clean built HTML from the temporary directory."""

    log = logging.getLogger('markdoc.clean-temp')

    if p.exists(config.temp_dir):
        log.debug('rm -Rf %s' % config.temp_dir)
        shutil.rmtree(config.temp_dir)

    log.debug('makedirs %s' % config.temp_dir)
    os.makedirs(config.temp_dir)


## Synchronization

@command
def sync_static(config, args):
    """Sync static files into the HTML root."""
    log = logging.getLogger('markdoc.sync-static')
    sync_html(config, args, True, log)

@command
def sync_html(config, args, only_static=False, log=None):
    """Sync built HTML and static media into the HTML root."""

    # the previous command used here was:
    # rsync -vaxq [--cvs-exclude] --delete --ignore-errors --include=.htaccess --exclude=.* \
    #       --exclude=_* temp_dir/ static_dir/ [default_template_dir/] html_dir/
    # it is unclear why both verbose and quiet were specified.

    log = log or logging.getLogger('markdoc.sync-html')

    exp = r'^(?:\..*|\_.*'

    if config['cvs-exclude']:
        # simulate --cvs-exclude (behavior from rsync man page)
        # use a couple groups to make the regex a little shorter,
        # and support both behaviors of path.join
        cvsexp = (r'RCS|SCCS|CVS(?:\.adm)?|RCSLOG|cvslog\..*|tags|TAGS|\.make\.state|\.nse_depinfo|.*~|'
                  r'#.*|\.#.*|,.*|_\$.*|.*\$|.*\.(?:bak|BAK)|.*\.orig|.*\.rej|\.del-.*|.*\.a|core|\.svn(?:/|\\)|'
                  r'.*\.o(?:bj|lb|ld)?|.*\.so|.*\.e(?:xe|lc)|.*\.Z|.*\.ln|\.git(?:/|\\)|\.hg(?:/|\\)|\.bzr(?:/|\\)')
        exp += "|" + cvsexp
        try:
            with open(os.path.expanduser(".cvsignore")) as cvs:
                cvsignore = cvs.readall().replace('\n', ' ').split()
                # convert fs globs to regexes and tack them on the end
                for glob in cvsignore:
                    exp += "|" + fnmatch.translate(glob)
        except IOError:
            log.debug("could not read ~/.cvsignore (might not exist)")

        cvsenv = os.environ.get("CVSIGNORE", "").split()
        for glob in cvsenv:
            exp += "|" + fnmatch.translate(glob)

    exp += ")$"
    log.debug("Regex: {0}".format(exp))
    # case insensitive on windows
    exp = re.compile(exp, re.I) if os.name == "nt" else re.compile(exp)

    if not p.exists(config.html_dir):
        log.debug('makedirs %s' % config.html_dir)
        os.makedirs(config.html_dir)

    html_dir = p.join(config.html_dir, '')

    # syncs a dir to the html dir
    # can't delete without knowing full merged structure of all three paths,
    # so return a structure of the filesystem copied by this call
    # optionally, use walk_only to only walk the directory and build the return,
    # don't copy anything
    def rsync(where_from, walk_only=False):
        copied_files = []
        # walk the dir and sync it
        for dirname, subdirnames, filenames in os.walk(p.join(where_from, '')):
            log.debug("entering {0}".format(dirname))
            # filter directories
            to_remove = []
            for subdirname in subdirnames:
                subdirname = p.join(p.basename(subdirname), '') # add trailing slash
                if exp.match(subdirname):
                    log.debug("removing subdirectory {0}".format(subdirname))
                    to_remove.append(p.dirname(subdirname)) # remove slash
            # remove directories from listing
            for subdir in to_remove:
                subdirnames.remove(subdir)

            # when the directories' basenames both start with a '.', this function captures that
            # so wrap in a dirname call to get only the first part
            prefix = p.dirname(p.commonprefix([html_dir, dirname]))
            # append the basename of where_from to make sure you don't get it in the output
            prefix = p.join(prefix, p.basename(p.dirname(p.join(where_from, ''))))
            # then cut all of that off the front of the dirname, make sure leading slash is not
            # included in second arg because that resets cwd
            target = p.join(html_dir, dirname[len(prefix)+1:])

            # add directory to filesystem
            copied_files.append(target)

            # copy files
            for filename in filenames:
                # .htaccess is an exception
                if not exp.match(p.basename(filename)) or p.basename(filename) == ".htaccess":
                    try:
                        if not p.exists(target):
                            log.debug("makedirs {0}".format(target))
                            os.makedirs(target)
                        log.debug("moving {0} to {1}".format(p.join(dirname, filename), target))
                        copied_files.append(p.join(target, filename))
                        if not walk_only:
                            # attempt to preserve permissions and other attributes as well as symlinks
                            shutil.copy2(p.join(dirname, filename), target)
                        else:
                            log.debug("(walked only)")
                    except (IOError, OSError, shutil.Error) as e:
                        # this is the --ignore-errors piece, I think
                        log.warning("file copy failed: {0}".format(e))
                else:
                    log.debug("skipping {0} (matched regex)".format(p.join(dirname, filename)))
        return copied_files

    filesystem = rsync(p.join(config.temp_dir, ''), walk_only=only_static)
    if config['use-default-static']:
        filesystem += rsync(p.join(markdoc.default_static_dir, ''))
    if p.isdir(config.static_dir):
        filesystem += rsync(p.join(config.static_dir, ''))

    # munge files to get rid of the /dir/./other_dir that shows up
    filesystem = [p.normpath(s) for s in filesystem]

    log.debug("mirroring filesystem to {0}: {1}".format(html_dir, filesystem))

    # delete files
    # ignore (but log) errors to comply with the old command's --ignore-errors flag
    for dirname, subdirnames, filenames in os.walk(html_dir):
        # start with directories
        deleted = []
        for subdir in subdirnames:
            full_subdir = p.join(dirname, subdir)
            if full_subdir not in filesystem and not exp.match(subdir):
                deleted.append(subdir)
                log.debug("recursively deleting directory {0}".format(full_subdir))
                # delete directory
                for root, dirs, files in os.walk(full_subdir, topdown=False):
                    for name in files:
                        try:
                            os.remove(os.path.join(root, name))
                        except (IOError, OSError, shutil.Error) as e:
                            log.warning("file delete failed: {0}".format(e))
                    for name in dirs:
                        try:
                            os.rmdir(os.path.join(root, name))
                        except (IOError, OSError, shutil.Error) as e:
                            log.warning("directory delete failed: {0}".format(e))
                try:
                    os.rmdir(full_subdir)
                except (IOError, OSError, shutil.Error) as e:
                    log.warning("directory delete failed: {0}".format(e))
        # don't walk deleted directories
        for subdir in deleted:
            subdirnames.remove(subdir)

        # delete files
        for filename in filenames:
            full_name = p.join(dirname, filename)
            if full_name not in filesystem and not exp.match(filename):
                log.debug("deleting file {0}".format(full_name))
                try:
                    os.remove(full_name)
                except (IOError, OSError, shutil.Error) as e:
                    log.warning("file delete failed: {0}".format(e))

    log.debug('sync complete')

## Building

@command
def build(config, args):
    """Compile wiki to HTML and sync to the HTML root."""

    log = logging.getLogger('markdoc.build')

    clean_temp(config, args)

    builder = Builder(config)
    for rel_filename in builder.walk():
        html = builder.render_document(rel_filename)
        out_filename = p.join(config.temp_dir,
                              p.splitext(rel_filename)[0] + p.extsep + 'html')

        if not p.exists(p.dirname(out_filename)):
            log.debug('makedirs %s' % p.dirname(out_filename))
            os.makedirs(p.dirname(out_filename))

        log.debug('Creating %s' % p.relpath(out_filename, start=config.temp_dir))
        fp = codecs.open(out_filename, 'w', encoding='utf-8')
        try:
            fp.write(html)
        finally:
            fp.close()

    sync_html(config, args)
    build_listing(config, args)


@command
def build_listing(config, args):
    """Create listings for all directories in the HTML root (post-build)."""

    log = logging.getLogger('markdoc.build-listing')

    list_basename = config['listing-filename']
    builder = Builder(config)
    generate_listing = config.get('generate-listing', 'always').lower()
    always_list = True
    if generate_listing == 'never':
        log.debug("No listing generated (generate-listing == never)")
        return  # No need to continue.

    for fs_dir, _, _ in os.walk(config.html_dir):
        index_file_exists = any([
            p.exists(p.join(fs_dir, 'index.html')),
            p.exists(p.join(fs_dir, 'index'))])

        directory = '/' + '/'.join(p.relpath(fs_dir, start=config.html_dir).split(p.sep))
        if directory == '/' + p.curdir:
            directory = '/'

        if (generate_listing == 'sometimes') and index_file_exists:
            log.debug("No listing generated for %s" % directory)
            continue

        log.debug("Generating listing for %s" % directory)
        listing = builder.render_listing(directory)
        list_filename = p.join(fs_dir, list_basename)

        fp = codecs.open(list_filename, 'w', encoding='utf-8')
        try:
            fp.write(listing)
        finally:
            fp.close()

        if not index_file_exists:
            log.debug("cp %s/%s %s/%s" % (directory, list_basename, directory, 'index.html'))
            shutil.copyfile(list_filename, p.join(fs_dir, 'index.html'))


## Serving

IPV4_RE = re.compile(r'^(25[0-5]|2[0-4]\d|[0-1]?\d?\d)(\.(25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}$')

@command
def serve(config, args):
    """Serve the built HTML from the HTML root."""

    # This should be a lazy import, otherwise it'll slow down the whole CLI.
    from markdoc.wsgi import MarkdocWSGIApplication

    log = logging.getLogger('markdoc.serve')
    app = MarkdocWSGIApplication(config)

    config['server.port'] = args.port
    config['server.num-threads'] = args.num_threads
    if args.server_name:
        config['server.name'] = args.server_name
    config['server.request-queue-size'] = args.queue_size
    config['server.timeout'] = args.timeout
    if args.interface:
        if not IPV4_RE.match(args.interface):
            serve.parser.error('invalid interface specifier: %r' % args.interface)
        config['server.bind'] = args.interface

    server = config.server_maker()(app)

    try:
        log.info('Serving on http://%s:%d' % server.bind_addr)
        server.start()
    except KeyboardInterrupt:
        log.debug('Interrupted')
    finally:
        log.info('Shutting down gracefully')
        server.stop()

serve.parser.add_argument('-p', '--port', type=int, default=8008,
                          help="Listen on specified port (default is 8008)")
serve.parser.add_argument('-i', '--interface', default=None,
                          help="Bind to specified interface (defaults to loopback only)")
serve.parser.add_argument('-t', '--num-threads', type=int, default=10, metavar='N',
                          help="Use N threads to handle requests (default is 10)")
serve.parser.add_argument('-n', '--server-name', default=None, metavar='NAME',
                          help="Use an explicit server name (default to an autodetected value)")
serve.parser.add_argument('-q', '--queue-size', type=int, default=5, metavar='SIZE',
                          help="Set request queue size (default is 5)")
serve.parser.add_argument('--timeout', type=int, default=10,
                          help="Set the socket timeout for connections (default is 10)")

