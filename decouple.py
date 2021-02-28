# coding: utf-8
import os
import sys
import string
from shlex import shlex
from io import open
from collections import OrderedDict
from distutils.util import strtobool

# Useful for very coarse version differentiation.
PY3 = sys.version_info[0] == 3

if PY3:
    from configparser import ConfigParser
    text_type = str
else:
    from ConfigParser import SafeConfigParser as ConfigParser
    text_type = unicode

DEFAULT_ENCODING = 'UTF-8'

class UndefinedValueError(Exception):
    pass


class UnsupportedParser(Exception):
    pass

class Undefined(object):
    """
    Class to represent undefined type.
    """
    pass


# Reference instance to represent undefined values
undefined = Undefined()


class Config(object):
    """
    Handle .env file format used by Foreman.
    """

    def __init__(self, repository):
        self.repository = repository

    def _cast_boolean(self, value):
        """
        Helper to convert config values to boolean as ConfigParser do.
        """
        value = str(value)
        return bool(value) if value == '' else bool(strtobool(value))

    @staticmethod
    def _cast_do_nothing(value):
        return value

    def get(self, option, default=undefined, cast=undefined):
        """
        Return the value for option or default if defined.
        """

        # We can't avoid __contains__ because value may be empty.
        if option in os.environ:
            value = os.environ[option]
        elif option in self.repository:
            value = self.repository[option]
        else:
            if isinstance(default, Undefined):
                raise UndefinedValueError('{} not found. Declare it as envvar or define a default value.'.format(option))

            value = default

        if isinstance(cast, Undefined):
            cast = self._cast_do_nothing
        elif cast is bool:
            cast = self._cast_boolean

        return cast(value)

    def __call__(self, *args, **kwargs):
        """
        Convenient shortcut to get.
        """
        return self.get(*args, **kwargs)


class WritableConfig(Config):

    def __init__(self, *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], WritableRepositoryIni):
            self.repository = args[0]
        else:
            self.repository = WritableRepositoryIni(*args, **kwargs)

    def get(self, option, default=undefined, cast=undefined):
        """
        Return the value for option or default if defined.
        """

        if option in self.repository:
            value = self.repository[option]
        else:
            if isinstance(default, Undefined):
                raise UndefinedValueError('{} not found. Declare it as envvar or define a default value.'.format(option))
            value = default

        if isinstance(cast, Undefined):
            cast = self._cast_do_nothing
        elif cast is bool:
            cast = self._cast_boolean

        return cast(value)

    def __getitem__(self, key):
        return self.get(key)

    def __contains__(self, key):
        return key in self.repository

    def __setitem__(self, key, value):
        self.repository[key] = value

    def __delitem__(self, key):
        del self.repository[key]

    def __delattr__(self, item):
        if item in ['section', 'SECTION']:
            del self.repository.SECTION


class RepositoryEmpty(object):
    def __init__(self, source='', encoding=DEFAULT_ENCODING):
        pass

    def __contains__(self, key):
        return False

    def __getitem__(self, key):
        return None


class RepositoryIni(RepositoryEmpty):
    """
    Retrieves option keys from .ini files.
    """
    SECTION = 'settings'

    def __init__(self, source, encoding=DEFAULT_ENCODING):
        self.parser = ConfigParser()
        with open(source, encoding=encoding) as file_:
            self.parser.read_file(file_)

    def __contains__(self, key):
        return (key in os.environ or
                self.parser.has_option(self.SECTION, key))

    def __getitem__(self, key):
        return self.parser.get(self.SECTION, key)


class WritableRepositoryIni(RepositoryIni):
    """
    RepositoryIni with write properties, and file and section creation
    """

    def __init__(self, source, section='default', create_section=True, encoding=DEFAULT_ENCODING):
        self.SECTION = str(section)
        self.source = source
        self.encoding = encoding

        if not os.path.exists(self.source):
            from pathlib import Path
            Path(self.source).touch()

        super(WritableRepositoryIni, self).__init__(self.source, encoding)

        if create_section and self.SECTION not in self.parser.sections():
            self.parser.add_section(self.SECTION)
            self._save()

    def _save(self):
        with open(self.source, 'w', encoding=self.encoding) as file_:
            self.parser.write(file_)

    def __contains__(self, key):
        return self.parser.has_option(self.SECTION, key)

    def __setitem__(self, key, value):
        self.parser.set(self.SECTION, key, str(value))
        self._save()

    def __delitem__(self, key):
        self.parser.remove_option(self.SECTION, key)
        self._save()

    def __delattr__(self, item):
        if item == 'SECTION':
            self.parser.remove_section(self.SECTION)
            self._save()

class RepositoryEnv(RepositoryEmpty):
    """
    Retrieves option keys from .env files with fall back to os.environ.
    """
    def __init__(self, source, encoding=DEFAULT_ENCODING):
        self.data = {}

        with open(source, encoding=encoding) as file_:
            for line in file_:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and ((v[0] == "'" and v[-1] == "'") or (v[0] == '"' and v[-1] == '"')):
                    v = v.strip('\'"')
                self.data[k] = v

    def __contains__(self, key):
        return key in os.environ or key in self.data

    def __getitem__(self, key):
        return self.data[key]


class AutoConfig(object):
    """
    Autodetects the config file and type.

    Parameters
    ----------
    search_path : str, optional
        Initial search path. If empty, the default search path is the
        caller's path.

    """
    SUPPORTED = OrderedDict([
        ('settings.ini', RepositoryIni),
        ('.env', RepositoryEnv),
    ])

    encoding = DEFAULT_ENCODING

    def __init__(self, search_path=None):
        self.search_path = search_path
        self.config = None

    def _find_file(self, path):
        # look for all files in the current path
        for configfile in self.SUPPORTED:
            filename = os.path.join(path, configfile)
            if os.path.isfile(filename):
                return filename

        # search the parent
        parent = os.path.dirname(path)
        if parent and parent != os.path.abspath(os.sep):
            return self._find_file(parent)

        # reached root without finding any files.
        return ''

    def _load(self, path):
        # Avoid unintended permission errors
        try:
            filename = self._find_file(os.path.abspath(path))
        except Exception:
            filename = ''
        Repository = self.SUPPORTED.get(os.path.basename(filename), RepositoryEmpty)

        self.config = Config(Repository(filename, encoding=self.encoding))

    def _caller_path(self):
        # MAGIC! Get the caller's module path.
        frame = sys._getframe()
        path = os.path.dirname(frame.f_back.f_back.f_code.co_filename)
        return path

    def __call__(self, *args, **kwargs):
        if not self.config:
            self._load(self.search_path or self._caller_path())

        return self.config(*args, **kwargs)


class CustomConfig(AutoConfig):
    """
    Specify the config file and type.

    Parameters
    ----------
    config_filename: str, required
        The name of the config file to load.
    repository_class: RepositoryEnv|RepositoryIni, required
        Class name of the parser, related to the type of the file.
    search_path : str, optional
        Initial search path. If empty, the default search path is the
        caller's path.
    section: str, optional
        For ini files, section name that should be loaded. The default is 
        "settings".

    """
    def __init__(self, config_filename, repository_class, search_path=None,
            section=None):

        if repository_class not in [RepositoryEnv, RepositoryIni]:
            raise UnsupportedParser("Unsupported Config Parser, should be : RepositoryEnv or RepositoryIni")
        self.SUPPORTED = {
            config_filename: repository_class
        }

        if section and repository_class == RepositoryIni:
            repository_class.SECTION = section

        super(CustomConfig, self).__init__(search_path)


# A pré-instantiated AutoConfig to improve decouple's usability
# now just import config and start using with no configuration.
config = AutoConfig()

# Helpers

class Csv(object):
    """
    Produces a csv parser that return a list of transformed elements.
    """

    def __init__(self, cast=text_type, delimiter=',', strip=string.whitespace, post_process=list):
        """
        Parameters:
        cast -- callable that transforms the item just before it's added to the list.
        delimiter -- string of delimiters chars passed to shlex.
        strip -- string of non-relevant characters to be passed to str.strip after the split.
        post_process -- callable to post process all casted values. Default is `list`.
        """
        self.cast = cast
        self.delimiter = delimiter
        self.strip = strip
        self.post_process = post_process

    def __call__(self, value):
        """The actual transformation"""
        transform = lambda s: self.cast(s.strip(self.strip))

        splitter = shlex(value, posix=True)
        splitter.whitespace = self.delimiter
        splitter.whitespace_split = True

        return self.post_process(transform(s) for s in splitter)


class Choices(object):
    """
    Allows for cast and validation based on a list of choices.
    """

    def __init__(self, flat=None, cast=text_type, choices=None):
        """
        Parameters:
        flat -- a flat list of valid choices.
        cast -- callable that transforms value before validation.
        choices -- tuple of Django-like choices.
        """
        self.flat = flat or []
        self.cast = cast
        self.choices = choices or []

        self._valid_values = []
        self._valid_values.extend(self.flat)
        self._valid_values.extend([value for value, _ in self.choices])


    def __call__(self, value):
        transform = self.cast(value)
        if transform not in self._valid_values:
            raise ValueError((
                    'Value not in list: {!r}; valid values are {!r}'
                ).format(value, self._valid_values))
        else:
            return transform
