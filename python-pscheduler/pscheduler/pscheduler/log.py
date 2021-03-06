"""
Functions for Logging
"""

import logging
import logging.handlers
import os
import pickle
import signal
import sys
import time
import traceback


# Logging Constants

# Priorities
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


# Facilities
## use getattr because this list is generated from syslog.h and may differ between systems.
auth = getattr(logging.handlers.SysLogHandler, "LOG_AUTH", None)
authpriv = getattr(logging.handlers.SysLogHandler, "LOG_AUTHPRIV", None)
cron = getattr(logging.handlers.SysLogHandler, "LOG_CRON", None)
daemon = getattr(logging.handlers.SysLogHandler, "LOG_DAEMON", None)
ftp = getattr(logging.handlers.SysLogHandler, "LOG_FTP", None)
kern = getattr(logging.handlers.SysLogHandler, "LOG_KERN", None)
lpr = getattr(logging.handlers.SysLogHandler, "LOG_LPR", None)
mail = getattr(logging.handlers.SysLogHandler, "LOG_MAIL", None)
news = getattr(logging.handlers.SysLogHandler, "LOG_NEWS", None)
syslog = getattr(logging.handlers.SysLogHandler, "LOG_SYSLOG", None)
user = getattr(logging.handlers.SysLogHandler, "LOG_USER", None)
uucp = getattr(logging.handlers.SysLogHandler, "LOG_UUCP", None)
local0 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL0", None)
local1 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL1", None)
local2 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL2", None)
local3 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL3", None)
local4 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL4", None)
local5 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL5", None)
local6 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL6", None)
local7 = getattr(logging.handlers.SysLogHandler, "LOG_LOCAL7", None)


# Internal-use name of environment variable
STATE_VARIABLE = 'PSCHEDULER_LOG_STATE'

class Log():

    """
    Logger class.

    Sendig SIGUSR1 will force the log level to DEBUG.  Sending SIGUSR2
    will set it to whatever level was previously set.

    If the 'propagate' parameter is True (which it is by default), the
    logging state (level, forced debug, quiet) will be passed along to
    any child process which instantiates a Log instance.  This happens
    via the environment, so anything that scrubs it clean (e.g., sudo)
    will cause this feaure not to function.
    """


    def __syslog_handler_deinit(self):
        """
        Kill off the syslog handler; called when a log event fails.
        """
        if self.syslog_handler is not None:
            self.logger.removeHandler(self.syslog_handler)
            self.syslog_handler = None


    def __syslog_handler_init(self):
        """
        Initialize the syslog handler if it hasn't been
        """
        if self.syslog_handler is None:
            try:
                # TODO: /dev/log is Linux-specific.
                self.syslog_handler = logging.handlers.SysLogHandler('/dev/log', facility=self.facility)
                self.syslog_handler.setFormatter(
                    logging.Formatter(
                        fmt = '%(name)s %(levelname)-8s %(message)s'
                    )
                )
                self.logger.addHandler(self.syslog_handler)
            except:
                self.__syslog_handler_deinit()



    def __init__(self,
                 name=None,     # Name for log entries
                 prefix=None,   # Prefix for name (e.g., prefix/progname)
                 level=INFO,    # Logging level
                 facility=local4, # Log facility
                 debug=False,   # Force level to DEBUG
                 verbose=False, # Log to stderr, too.
                 quiet=None ,   # Don't log anything on startup  (See below)
                 signals=True,  # Enable debug on/off with SIGUSR1/SIGUSR2
                 propagate=True # Pass debug state on to child processes
                 ):

        #
        # Handle the parameters
        #

        if name is None:
            name = os.path.basename(sys.argv[0])
        assert type(name) == str

        if prefix is not None:
            assert type(prefix) == str
            name = prefix + "/" + name

        self.facility = facility

        if debug:
            level = DEBUG

        self.is_propagating = propagate
        self.is_propagating = True

        # This prevents verbose() from choking on this being undefined.
        self.is_verbose = False

        if quiet is None:
            quiet = False
            forced_quiet = False
        else:
            forced_quiet = quiet

        self.is_quiet = quiet

        self.forced_debug = False

        #
        # Inherit state from the environment 
        #

        if STATE_VARIABLE in os.environ:

            try:
                depickled = pickle.loads(os.environ[STATE_VARIABLE])

                facility = depickled['facility']
                assert type(facility) == int

                level = depickled['last_level']
                assert type(level) == int

                self.forced_debug = depickled['forced_debug']
                assert type(self.forced_debug) == bool

                self.is_quiet = depickled['is_quiet']
                assert type(self.is_quiet) == bool

            except Exception as ex:
                self.exception("Failed to decode %s '%s'" \
                                   % (STATE_VARIABLE, os.environ[STATE_VARIABLE]))

        #
        # Set up the logger
        #

        self.logger = logging.getLogger(name)
        self.logger.propagate = False

        self.syslog_handler = None
        self.__syslog_handler_init()

        # Stderr
        self.stderr_handler = logging.StreamHandler(sys.stderr)
        self.stderr_handler.setFormatter(
            logging.Formatter(
                fmt     = '%(asctime)s %(message)s',
                datefmt = '%Y-%m-%dT%H:%M:%S'
                )
            )
        # Don't add this handler; verbose will cover it.

        #
        # Get everything set to go
        #

        self.verbose(verbose)
        self.level(level)
        self.set_debug(self.forced_debug)
        self.__update_env()

        # Grab signals and make them non-interrupting
        # TODO: How portable is this?
        if signals:
            signal.signal(signal.SIGUSR1, self.sigusr1)
            signal.signal(signal.SIGUSR2, self.sigusr2)
            signal.siginterrupt(signal.SIGUSR1, False)
            signal.siginterrupt(signal.SIGUSR2, False)


        if (not self.is_quiet) and (not forced_quiet):
            self.info("Started")



    def __del__(self):
        if self is not None:
            self.__syslog_handler_deinit()


    def __update_env(self):
        """
        (INTERNAL USE ONLY) Update the environment variable passed to
        child processes to pre-set the state.
        """
        if self.is_propagating:
            to_pickle = {
                'forced_debug': self.forced_debug,
                'facility': self.facility,
                'last_level': self.last_level,
                'is_quiet': self.is_quiet
                }
            os.environ[STATE_VARIABLE] = pickle.dumps(to_pickle)




    def verbose(self, state):
        "Toggle verbosity (logging to stderr)"
        if state == self.is_verbose:
            return

        if state:
            self.logger.addHandler(self.stderr_handler)
        else:
            self.logger.removeHandler(self.stderr_handler)

        self.is_verbose = state


    def facility(self, facility):
        "Set the log level"
        assert level in [ DEBUG, INFO, WARNING, ERROR, CRITICAL ]
        self.logger.setLevel(level)
        if save:
            self.last_level = level
        self.__update_env()


    def level(self, level, save=True):
        "Set the log level"
        assert level in [ DEBUG, INFO, WARNING, ERROR, CRITICAL ]
        self.logger.setLevel(level)
        if save:
            self.last_level = level
        self.__update_env()


    # Logging


    def log(self, level, format, *args):
        self.__syslog_handler_init()
        try:
            self.logger.log(level, format, *args)
        except Exception:
            self.__syslog_handler_deinit()

    def debug(self, format, *args):
        self.log(DEBUG, format, *args)

    def info(self, format, *args):
        self.log(INFO, format, *args)

    def warning(self, format, *args):
        self.log(WARNING, format, *args)

    def error(self, format, *args):
        self.log(ERROR, format, *args)

    def critical(self, format, *args):
        self.log(CRITICAL, format, *args)

    def exception(self, message=None):
        "Log an exception as an error and debug if we're doing that."
        extype, ex, tb = sys.exc_info()
        message = "Exception: %s%s%s" % (
            message+': ' if message is not None else '',
            ''.join(traceback.format_exception_only(extype, ex)),
            ''.join(traceback.format_exception(extype, ex, tb)).strip()
            )
        if self.forced_debug:
            self.debug(message)
        else:
            self.error(message)



    # Forced setting of debug level

    def sigusr1(self, signum, frame):
        self.set_debug(True)

    def sigusr2(self, signum, frame):
        self.set_debug(False)

    def set_debug(self, state):
        "Turn debugging on or off, remembering the last-set level"

        if state == self.forced_debug:
            self.debug("Debug signal ignored; already %sdebugging",
                       "" if state else "not ")

        if state:
            self.level(DEBUG, save=False)
            self.debug("Debug started")
        else:
            self.debug("Debug discontinued")
            self.level(self.last_level)

        self.forced_debug = state
        self.__update_env()



# Test program

if __name__ == "__main__":

    log = Log(verbose=True, prefix='test')

    log.debug("Invisible debug.")

    try:
        raise ValueError("Test exception")
    except Exception as ex:
        log.exception("Test exception with message")

    for num in range(1,5):
        log.debug("Debug")
        log.info("Info")
        log.warning("Warning")
        log.error("Error")
        log.critical("Crtitical")
        os.kill( os.getpid(),
                 signal.SIGUSR1 if (num % 2) != 0 else signal.SIGUSR2 )
        time.sleep(1)
