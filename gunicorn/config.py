# -*- coding: utf-8 -
#
# This file is part of gunicorn released under the MIT license. 
# See the NOTICE for more information.

import copy
import grp
import inspect
import optparse
import os
import pwd
import sys
import textwrap
import types

from gunicorn import __version__
from gunicorn.errors import ConfigError
from gunicorn import util

KNOWN_SETTINGS = []

def wrap_method(func):
    def _wrapped(instance, *args, **kwargs):
        return func(*args, **kwargs)
    return _wrapped

def make_settings(ignore=None):
    settings = {}
    ignore = ignore or ()
    for s in KNOWN_SETTINGS:
        setting = s()
        if setting.name in ignore:
            continue
        settings[setting.name] = setting.copy()
    return settings

class Config(object):
        
    def __init__(self, usage=None):
        self.settings = make_settings()
        self.usage = usage
        
    def __getattr__(self, name):
        if name not in self.settings:
            raise AttributeError("No configuration setting for: %s" % name)
        return self.settings[name].get()
    
    def __setattr__(self, name, value):
        if name != "settings" and name in self.settings:
            raise AttributeError("Invalid access!")
        super(Config, self).__setattr__(name, value)
    
    def set(self, name, value):
        if name not in self.settings:
            raise AttributeError("No configuration setting for: %s" % name)
        self.settings[name].set(value)

    def parser(self):
        kwargs = {
            "usage": self.usage,
            "version": __version__
        }
        parser = optparse.OptionParser(**kwargs)

        keys = self.settings.keys()
        def sorter(k):
            return (self.settings[k].section, self.settings[k].order)
        keys.sort(key=sorter)
        for k in keys:
            self.settings[k].add_option(parser)
        return parser

    @property
    def worker_class(self):
        uri = self.settings['worker_class'].get()
        worker_class = util.load_worker_class(uri)
        if hasattr(worker_class, "setup"):
            worker_class.setup()
        return worker_class

    @property   
    def workers(self):
        return self.settings['workers'].get()

    @property
    def address(self):
        bind = self.settings['bind'].get()
        return util.parse_address(util.to_bytestring(bind))
        
    @property
    def uid(self):
        return self.settings['user'].get()
      
    @property
    def gid(self):
        return self.settings['group'].get()
        
    @property
    def proc_name(self):
        pn = self.settings['proc_name'].get()
        if pn is not None:
            return pn
        else:
            return self.settings['default_proc_name'].get()
            
class SettingMeta(type):
    def __new__(cls, name, bases, attrs):
        super_new = super(SettingMeta, cls).__new__
        parents = [b for b in bases if isinstance(b, SettingMeta)]
        if not parents:
            return super_new(cls, name, bases, attrs)
    
        attrs["order"] = len(KNOWN_SETTINGS)
        attrs["validator"] = wrap_method(attrs["validator"])
        
        new_class = super_new(cls, name, bases, attrs)
        new_class.fmt_desc(attrs.get("desc", ""))
        KNOWN_SETTINGS.append(new_class)
        return new_class

    def fmt_desc(cls, desc):
        desc = textwrap.dedent(desc).strip()
        setattr(cls, "desc", desc)
        setattr(cls, "short", desc.splitlines()[0])

class Setting(object):
    __metaclass__ = SettingMeta
    
    name = None
    value = None
    section = None
    cli = None
    validator = None
    type = None
    meta = None
    action = None
    default = None
    short = None
    desc = None
    
    def __init__(self):
        if self.default is not None:
            self.set(self.default)    
        
    def add_option(self, parser):
        if not self.cli:
            return
        args = tuple(self.cli)
        kwargs = {
            "dest": self.name,
            "metavar": self.meta or None,
            "action": self.action or "store",
            "type": self.type or "string",
            "default": None,
            "help": "%s [%s]" % (self.short, self.default)
        }
        if kwargs["action"] != "store":
            kwargs.pop("type")
        parser.add_option(*args, **kwargs)
    
    def copy(self):
        return copy.copy(self)
    
    def get(self):
        return self.value
    
    def set(self, val):
        assert callable(self.validator), "Invalid validator: %s" % self.name
        self.value = self.validator(val)

def validate_bool(val):
    if isinstance(val, types.BooleanType):
        return val
    if not isinstance(val, basestring):
        raise TypeError("Invalid type for casting: %s" % val)
    if val.lower().strip() == "true":
        return True
    elif val.lower().strip() == "false":
        return False
    else:
        raise ValueError("Invalid boolean: %s" % val)

def validate_pos_int(val):
    if not isinstance(val, (types.IntType, types.LongType)):
        val = int(val, 0)
    else:
        # Booleans are ints!
        val = int(val)
    if val < 0:
        raise ValueError("Value must be positive: %s" % val)
    return val

def validate_string(val):
    if val is None:
        return None
    if not isinstance(val, basestring):
        raise TypeError("Not a string: %s" % val)
    return val.strip()

def validate_callable(arity):
    def _validate_callable(val):
        if not callable(val):
            raise TypeError("Value is not callable: %s" % val)
        if arity != len(inspect.getargspec(val)[0]):
            raise TypeError("Value must have an arity of: %s" % arity)
        return val
    return _validate_callable


def validate_user(val):
    if val is None:
        return os.geteuid()
    elif val.isdigit() or isinstance(val, int):
        return int(val)
    else:
        try:
            return pwd.getpwnam(val).pw_uid
        except KeyError:
            raise ConfigError("No such user: '%s'" % val)

def validate_group(val):
    if val is None:
        return os.getegid()
    elif val.isdigit() or isinstance(val, int):
        return int(val)
    else:
        try:
            return grp.getgrnam(val).gr_gid
        except KeyError:
            raise ConfigError("No such group: '%s'" % val)

class ConfigFile(Setting):
    name = "config"
    section = "Config File"
    cli = ["-c", "--config"]
    meta = "FILE"
    validator = validate_string
    default = None
    desc = """\
        The path to a Gunicorn config file.
        
        Only has an effect when specified on the command line or as part of an
        application specific configuration.    
        """

class Bind(Setting):
    name = "bind"
    section = "Server Socket"
    cli = ["-b", "--bind"]
    meta = "ADDRESS"
    validator = validate_string
    default = "127.0.0.1:8000"
    desc = """\
        The socket to bind.
        
        A string of the form: 'HOST', 'HOST:PORT', 'unix:PATH'. An IP is a valid
        HOST.
        """
        
class Backlog(Setting):
    name = "backlog"
    section = "Server Socket"
    cli = ["--backlog"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 2048
    desc = """\
        The maximum number of pending connections.    
        
        This refers to the number of clients that can be waiting to be served.
        Exceeding this number results in the client getting an error when
        attempting to connect. It should only affect servers under significant
        load.
        
        Must be a positive integer. Generally set in the 64-2048 range.    
        """

class Workers(Setting):
    name = "workers"
    section = "Worker Processes"
    cli = ["-w", "--workers"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 1
    desc = """\
        The number of worker process for handling requests.
        
        A positive integer generally in the 2-4 x $(NUM_CORES) range. You'll
        want to vary this a bit to find the best for your particular
        application's work load.
        """

class WorkerClass(Setting):
    name = "worker_class"
    section = "Worker Processes"
    cli = ["-k", "--worker-class"]
    meta = "STRING"
    validator = validate_string
    default = "sync"
    desc = """\
        The type of workers to use.
        
        The default class (sync) should handle most 'normal' types of workloads.
        You'll want to read http://gunicorn.org/design.html for information on
        when you might want to choose one of the other worker classes.
        
        A string referring to one of the following bundled classes:
        
        * ``sync``
        * ``eventlet`` - Requires eventlet >= 0.9.7
        * ``gevent``   - Requires gevent >= 0.12.2 (?)
        * ``tornado``  - Requires tornado >= 0.2
        
        Optionally, you can provide your own worker by giving gunicorn a
        MODULE:CLASS pair where CLASS is a subclass of
        gunicorn.workers.base.Worker. This alternative syntax will load the
        gevent class: ``egg:gunicorn#gevent``
        """

class WorkerConnections(Setting):
    name = "worker_connections"
    section = "Worker Processes"
    cli = ["--worker-connections"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 1000
    desc = """\
        The maximum number of simultaneous clients.
        
        This setting only affects the Eventlet and Gevent worker types.
        """

class MaxRequests(Setting):
    name = "max_requests"
    section = "Worker Processes"
    cli = ["--max-requests"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 0
    desc = """\
        The maximum number of requests a worker will process before restarting.
        
        Any value greater than zero will limit the number of requests a work
        will process before automatically restarting. This is a simple method
        to help limit the damage of memory leaks.
        
        If this is set to zero (the default) then the automatic worker
        restarts are disabled.
        """

class Timeout(Setting):
    name = "timeout"
    section = "Worker Processes"
    cli = ["-t", "--timeout"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 30
    desc = """\
        Workers silent for more than this many seconds are killed and restarted.
        
        Generally set to thirty seconds. Only set this noticeably higher if
        you're sure of the repercussions for sync workers. For the non sync
        workers it just means that the worker process is still communicating and
        is not tied to the length of time required to handle a single request.
        """

class Keepalive(Setting):
    name = "keepalive"
    section = "Worker Processes"
    cli = ["--keep-alive"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 2
    desc = """\
        The number of seconds to wait for requests on a Keep-Alive connection.
        
        Generally set in the 1-5 seconds range.    
        """

class Debug(Setting):
    name = "debug"
    section = "Debugging"
    cli = ["--debug"]
    validator = validate_bool
    action = "store_true"
    default = False
    desc = """\
        Turn on debugging in the server.
        
        This limits the number of worker processes to 1 and changes some error
        handling that's sent to clients.
        """

class Spew(Setting):
    name = "spew"
    section = "Debugging"
    cli = ["--spew"]
    validator = validate_bool
    action = "store_true"
    default = False
    desc = """\
        Install a trace function that spews every line executed by the server.
        
        This is the nuclear option.    
        """

class PreloadApp(Setting):
    name = "preload_app"
    section = "Server Mechanics"
    cli = ["--preload"]
    validator = validate_bool
    action = "store_true"
    default = False
    desc = """\
        Load application code before the worker processes are forked.
        
        By preloading an application you can save some RAM resources as well as
        speed up server boot times. Although, if you defer application loading
        to each worker process, you can reload your application code easily by
        restarting workers.
        """

class Daemon(Setting):
    name = "daemon"
    section = "Server Mechanics"
    cli = ["-D", "--daemon"]
    validator = validate_bool
    action = "store_true"
    default = False
    desc = """\
        Daemonize the Gunicorn process.
        
        Detaches the server from the controlling terminal and enters the
        background.
        """

class Pidfile(Setting):
    name = "pidfile"
    section = "Server Mechanics"
    cli = ["-p", "--pid"]
    meta = "FILE"
    validator = validate_string
    default = None
    desc = """\
        A filename to use for the PID file.
        
        If not set, no PID file will be written.
        """

class User(Setting):
    name = "user"
    section = "Server Mechanics"
    cli = ["-u", "--user"]
    meta = "USER"
    validator = validate_user
    default = None
    desc = """\
        Switch worker processes to run as this user.
        
        A valid user id (as an integer) or the name of a user that can be
        retrieved with a call to pwd.getpwnam(value) or None to not change
        the worker process user.
        """

class Group(Setting):
    name = "group"
    section = "Server Mechanics"
    cli = ["-g", "--group"]
    meta = "GROUP"
    validator = validate_group
    default = None
    desc = """\
        Switch worker process to run as this group.
        
        A valid group id (as an integer) or the name of a user that can be
        retrieved with a call to pwd.getgrnam(value) or None to not change
        the worker processes group.
        """

class Umask(Setting):
    name = "umask"
    section = "Server Mechanics"
    cli = ["-m", "--umask"]
    meta = "INT"
    validator = validate_pos_int
    type = "int"
    default = 0
    desc = """\
        A bit mask for the file mode on files written by Gunicorn.
        
        Note that this affects unix socket permissions.
        
        A valid value for the os.umask(mode) call or a string compatible with
        int(value, 0) (0 means Python guesses the base, so values like "0",
        "0xFF", "0022" are valid for decimal, hex, and octal representations)
        """

class TmpUploadDir(Setting):
    name = "tmp_upload_dir"
    section = "Server Mechanics"
    meta = "DIR"
    validator = validate_string
    default = None
    desc = """\
        Directory to store temporary request data as they are read.
        
        This may disappear in the near future.
        
        This path should be writable by the process permissions set for Gunicorn
        workers. If not specified, Gunicorn will choose a system generated
        temporary directory.
        """

class Logfile(Setting):
    name = "logfile"
    section = "Logging"
    cli = ["--log-file"]
    meta = "FILE"
    validator = validate_string
    default = "-"
    desc = """\
        The log file to write to.
        
        "-" means log to stdout.
        """

class Loglevel(Setting):
    name = "loglevel"
    section = "Logging"
    cli = ["--log-level"]
    meta = "LEVEL"
    validator = validate_string
    default = "info"
    desc = """\
        The granularity of log outputs.
        
        Valid level names are:
        
        * debug
        * info
        * warning
        * error
        * critical
        """

class LogConfig(Setting):
    name = "logconfig"
    section = "Logging"
    cli = ["--log-config"]
    meta = "FILE"
    validator = validate_string
    default = None 
    desc = """\
        The log config file to use.
        
        Gunicorn uses the standard Python logging module's Configuration
        file format.
        """

class Procname(Setting):
    name = "proc_name"
    section = "Process Naming"
    cli = ["-n", "--name"]
    meta = "STRING"
    validator = validate_string
    default = None
    desc = """\
        A base to use with setproctitle for process naming.
        
        This affects things like ``ps`` and ``top``. If you're going to be
        running more than one instance of Gunicorn you'll probably want to set a
        name to tell them apart. This requires that you install the setproctitle
        module.
        
        It defaults to 'gunicorn'.
        """

class DefaultProcName(Setting):
    name = "default_proc_name"
    section = "Process Naming"
    validator = validate_string
    default = "gunicorn"
    desc = """\
        Internal setting that is adjusted for each type of application.
        """

class WhenReady(Setting):
    name = "when_ready"
    section = "Server Hooks"
    validator = validate_callable(1)
    type = "callable"
    def def_start_server(server):
        pass
    def_start_server = staticmethod(def_start_server)
    default = def_start_server
    desc = """\
        Called just after the server is started.
        
        The callable needs to accept a single instance variable for the Arbiter.
        """

class Prefork(Setting):
    name = "pre_fork"
    section = "Server Hooks"
    validator = validate_callable(2)
    type = "callable"
    def def_pre_fork(server, worker):
        pass
    def_pre_fork = staticmethod(def_pre_fork)
    default = def_pre_fork
    desc = """\
        Called just before a worker is forked.
        
        The callable needs to accept two instance variables for the Arbiter and
        new Worker.
        """
    
class Postfork(Setting):
    name = "post_fork"
    section = "Server Hooks"
    validator = validate_callable(2)
    type = "callable"
    def def_post_fork(server, worker):
        pass
    def_post_fork = staticmethod(def_post_fork)
    default = def_post_fork
    desc = """\
        Called just after a worker has been forked.
        
        The callable needs to accept two instance variables for the Arbiter and
        new Worker.
        """

class PreExec(Setting):
    name = "pre_exec"
    section = "Server Hooks"
    validator = validate_callable(1)
    type = "callable"
    def def_pre_exec(server):
        pass
    def_pre_exec = staticmethod(def_pre_exec)
    default = def_pre_exec
    desc = """\
        Called just before a new master process is forked.
        
        The callable needs to accept a single instance variable for the Arbiter.
        """

class PreRequest(Setting):
    name = "pre_request"
    section = "Server Hooks"
    validator = validate_callable(2)
    type = "callable"
    def def_pre_request(worker, req):
        worker.log.debug("%s %s" % (req.method, req.path))
    def_pre_request = staticmethod(def_pre_request)
    default = def_pre_request
    desc = """\
        Called just before a worker processes the request.
        
        The callable needs to accept two instance variables for the Worker and
        the Request.
        """

class PostRequest(Setting):
    name = "post_request"
    section = "Server Hooks"
    validator = validate_callable(2)
    type = "callable"
    def def_post_request(worker, req):
        pass
    def_post_request = staticmethod(def_post_request)
    default = def_post_request
    desc = """\
        Called after a worker processes the request.

        The callable needs to accept two instance variables for the Worker and
        the Request.
        """

class WorkerExit(Setting):
    name = "worker_exit"
    section = "Server Hooks"
    validator = validate_callable(2)
    type = "callable"
    def def_worker_exit(server, worker):
        pass
    def_worker_exit = staticmethod(def_worker_exit)
    default = def_worker_exit
    desc = """\
        Called just after a worker has been exited.

        The callable needs to accept two instance variables for the Arbiter and
        the just-exited Worker.
        """
