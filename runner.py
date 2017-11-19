from argparse import ArgumentParser, Action, SUPPRESS
import os
import textwrap as tw
import itertools
import json
import hashlib
import platform

def generate_unique_id(d, N=10):
    """
    Return a unique hash string for the dictionary ``d``.

    Parameters
    ----------
    d : dict
        the dictionary of meta-data to use to make the hash
    N : int, optional
        return the first ``N`` characters from the hash string
    """
    import hashlib

    s = json.dumps(d, sort_keys=True).encode()
    return hashlib.sha1(s).hexdigest()[:N]

def get_machine_info():
    """
    Return information about the machine, including host,
    system, and the python version.
    """
    return {'host':platform.node(),
            'system': platform.system(),
            'python_version': ".".join(platform.python_version_tuple())}

def parametrize(params):
    """
    Execute a function for each of the input parameters.
    """
    keys = list(params.keys())
    params = list(itertools.product(*[params[k] for k in params]))
    def wrapped(func):
        def func_wrapper(*args, **kwargs):
            for p in params:
                kwargs.update(dict(zip(keys, p)))
                func(*args, **kwargs)

        return func_wrapper
    return wrapped

def InfoAction(runner):

    class _InfoAction(Action):
        """
        Action similar to ``help`` to print out
        the various registered commands for the ``BenchmarkRunner``.
        class
        """
        def __init__(self,
                     option_strings,
                     dest=SUPPRESS,
                     default=SUPPRESS,
                     help=None):
            super(_InfoAction, self).__init__(
                option_strings=option_strings,
                dest=dest,
                default=default,
                nargs=0,
                help=help)

        def __call__(self, parser, namespace, values, option_string=None):

            print("Registered commands\n" + "-"*19)
            for i, command in enumerate(runner.commands):
                tag = runner.tags[i]
                c = tw.dedent(command).strip()
                c = tw.fill(c, initial_indent=' '*4, subsequent_indent=' '*4, width=80)

                header = "%d:" %i
                if len(tag):
                    header = header + " " + ", ".join(["'%s' = %s" %(k,tag[k]) for k in tag])
                header += "\n"
                print("%s\n%s\n" %(header, c))

            parser.exit()

    return _InfoAction

class BenchmarkRunner(object):
    """
    Class to run ``benchmark.py`` commands in a reproducible manner.

    Parameters
    ----------
    test_path : str
        the path of the test module in the nbodykit source code we
        are running, e.g., ``benchmarks/test_fftpower.py``
    result_dir : str
        the directory where we want to save the results, passed via
        ``--bench-dir``
    """
    samples = ['boss', 'desi']

    def __init__(self, test_path, result_dir):
        self.test_path = test_path
        self.result_dir = result_dir

        # track commands and tags for each
        self.commands = []
        self.tags = []

    def add_commands(self, testnames, ncores):
        """
        Register benchmarks for different configurations of
        test names and number of cores.

        Parameters
        ----------
        testnames : list of str
            a list of the test functions we want to run
        ncores : list of int
            a list of the number of cores we want to run
        """
        @parametrize({'sample': self.samples, 'testname':testnames, 'ncores':ncores})
        def _add_commands(sample, testname, ncores):

            # the name of the benchmark test to run
            bench_name = self.test_path + "::" + testname

            # the output directory
            bench_dir = os.path.join(self.result_dir, sample, str(ncores))

            # make the command
            args = (bench_name, sample, bench_dir, ncores)
            cmd = "python ../benchmark.py {} --sample {} --bench-dir {} -n {}".format(*args)

            # and register
            tag = {'sample':sample, 'testname':testname, 'ncores':ncores}
            self.register(cmd, tag=tag)

        # add the commands
        _add_commands()

    def register(self, command, tag={}):
        """
        Register a new command with the specified tag.
        """
        self.commands.append(command)
        self.tags.append(tag)

    def execute(self):
        """
        Execute the ``BenchmarkRunner`` command.
        """
        # parse and get the command
        ns, unknown = self.parse_args()

        # setup the output directory
        self._store_config()

        # append unknown command-line args
        command = self.commands[ns.testno] + ' ' + ' '.join(unknown)

        # execute
        self._execute(command)

    def _store_config(self):
        """
        Internal function to store the configuration.
        """
        # setup the hash and
        machine_info = get_machine_info()
        hashstr = generate_unique_id(machine_info)

        # update the result directory
        self.result_dir = os.path.join(self.result_dir, hashstr)
        if not os.path.exists(self.result_dir):
            os.makedirs(self.result_dir)

        # dump config to JSON file, if it doesnt exist
        cfg_file = os.path.join(self.result_dir, 'config.json')
        if not os.path.exists(cfg_file):
            json.dump(machine_info, open(cfg_file, 'w'))


    def _execute(self, command):
        """
        Internal function to execute the command.

        Parameters
        ----------
        command : str
            the command to execute
        """
        # print the command
        c = tw.dedent(command).strip()
        c = tw.fill(c, initial_indent=' '*4, subsequent_indent=' '*4, width=80)
        print("executing:\n%s" %c)

        # execute
        os.system(command)

    def parse_args(self):
        """
        Parse the command-line arguments
        """
        desc = "run the ``benchmarks.py`` script from a set of registered commands"
        parser = ArgumentParser(description=desc)

        h = 'the integer number of the test to run'
        parser.add_argument('testno', type=int, help=h)

        h = 'print out the various commands'
        parser.add_argument('-i', '--info', action=InfoAction(self), help=h)

        ns, unknown = parser.parse_known_args()

        # make sure the integer value is valid
        if not (0 <= ns.testno < len(self.commands)):
            N = len(self.commands)
            raise ValueError("input ``testno`` must be between [0, %d]" %N)

        return ns, unknown
