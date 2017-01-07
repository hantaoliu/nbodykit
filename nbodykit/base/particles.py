from nbodykit.extern.six import add_metaclass

import abc
import numpy
import logging

def column(name=None):
    def decorator(getter):
        getter.column_name = name
        return getter

    if hasattr(name, '__call__'):
        # a single callable is provided
        getter = name
        name = getter.__name__
        return decorator(getter)
    else:
        return decorator

def find_columns(cls):
    hardcolumns = []
    for key, value in cls.__dict__.items():
         if hasattr(value, 'column_name'):
            hardcolumns.append(value.column_name)

    return list(sorted(set(hardcolumns)))

def find_column(cls, name):
    for key, value in cls.__dict__.items():
        if not hasattr(value, 'column_name'): continue
        if value.column_name == name: return value
    raise AttributeError("Column %s not found in class %s." % (str(cls), name))

@add_metaclass(abc.ABCMeta)
class ParticleSource(object):
    """
    Base class for a source of input particles
    
    This combines the process of reading and painting
    """
    logger = logging.getLogger('ParticleSource')

    @staticmethod
    def make_column(array):
        """ convert a numpy array to a column object (dask.array.Array) """
        import dask.array as da
        return da.from_array(array, chunks=getattr(array, 'chunks', 100000))

    # called by the subclasses
    def __init__(self, comm):
        # ensure self.comm is set, though usually already set by the child.

        self.comm = comm

        # initial dicts of overrided and fallback columns
        self._overrides = {}
        self._fallbacks = {}
        
        # if size is already computed update csize
        # otherwise the subclass shall call update_csize explicitly.
        if self.size is not NotImplemented:
            self.update_csize()

        self._overrides = {}

    def to_mesh(self, Nmesh=None, BoxSize=None, dtype='f4',
            interlaced=False, compensated=False, window='cic',
            weight='Weight', selection='Selection'
             ):
        """ 
            Convert the ParticleSource to a MeshSource

            FIXME: probably add Position, Weight and Selection column names

        """
        from nbodykit.base.particlemesh import ParticleMeshSource

        if BoxSize is None:
            try:
                BoxSize = self.attrs['BoxSize']
            except KeyError:
                raise ValueError("BoxSize is not supplied but the particle source does not define one in attrs.")

        if Nmesh is None:
            try:
                Nmesh = self.attrs['Nmesh']
            except KeyError:
                raise ValueError("Nmesh is not supplied but the particle source does not define one in attrs.")

        r = ParticleMeshSource(self, Nmesh=Nmesh, BoxSize=BoxSize, dtype=dtype, weight=weight, selection=selection)

        r.interlaced = interlaced
        r.compensated = compensated
        r.window = window
        return r

    def update_csize(self):
        """ set the collective size

            Call this function in __init__ of subclass, 
            after .size is a valid value (not NotImplemented)
        """
        self._csize = self.comm.allreduce(self.size)

        if self.comm.rank == 0:
            self.logger.debug("rank 0, local number of particles = %d" % self.size)
            self.logger.info("total number of particles = %d" % self.csize)

        import dask.array as da

        self._fallbacks = {
                'Selection': da.ones(self.size, dtype='?', chunks=100000),
                   'Weight': da.ones(self.size, dtype='?', chunks=100000),
                          }

    @property
    def attrs(self):
        """
        Dictionary storing relevant meta-data
        """
        try:
            return self._attrs
        except AttributeError:
            self._attrs = {}
            return self._attrs

    @staticmethod
    def compute(*args, **kwargs):
        """
        Our version of :func:`dask.compute` that computes
        multiple delayed dask collections at once
        
        This should be called on the return value of :func:`read`
        to converts any dask arrays to numpy arrays
        
        Parameters
        -----------
        args : object
            Any number of objects. If the object is a dask 
            collection, it's computed and the result is returned. 
            Otherwise it's passed through unchanged.
        
        Notes
        -----
        The dask default optimizer induces too many (unnecesarry) 
        IO calls -- we turn this off feature off by default.
        
        Eventually we want our own optimizer probably.
        """
        import dask
        
        # XXX find a better place for this function
        kwargs.setdefault('optimize_graph', False)
        return dask.compute(*args, **kwargs)

    def __len__(self):
        """
        The length of ParticleSource is equal to :attr:`size`; this is the 
        local size of the source on a given rank
        """
        return self.size

    def __contains__(self, col):
        return col in self.columns

    @property
    def hardcolumns(self):
        """ a list of hard coded columns.
            These are usually member functions marked by @column decorator.

            Subclasses may override this method and get_hardcolumn to bypass
            the decorator logic.
        """
        try:
            self._hardcolumns
        except AttributeError:
            self._hardcolumns = find_columns(self.__class__)
        return self._hardcolumns

    def get_hardcolumn(self, col):
        """ construct and return a hard coded column.
            These are usually produced by calling member functions marked by @column decorator.

            Subclasses may override this method and the hardcolumns attribute to bypass
            the decorator logic.
        """
        return find_column(self.__class__, col)(self)


    @property
    def columns(self):
        """
        The names of the data fields defined for each particle, including overriden columns and fallback columns
        """
        return sorted(set(list(self.hardcolumns) + list(self._overrides) + list(self._fallbacks)))

    @property
    def csize(self):
        """
        The collective size of the source, i.e., summed across all ranks
        """
        return self._csize

    @abc.abstractproperty
    def size(self):
        """
        The number of particles in the source on the local rank.
        """
        return NotImplemented

    def __getitem__(self, col):
        if col in self._overrides:
            r = self._overrides[col]
        elif col in self.hardcolumns:
            r = self.get_hardcolumn(col)
        elif col in self._fallbacks:
            r = self._fallbacks[col]
        else:
            raise KeyError("column `%s` is not defined in this source" % col)
        if not hasattr(r, 'attrs'):
            r.attrs = {}
        return r

    def save(self, output, columns, datasets=None, header='Header'):
        """ Save the data source to a bigfile.

            selected columns are saved. attrs are saved in header.
            attrs of columns are stored in the datasets.
        """
        import bigfile
        if datasets is None:
            datasets = columns

        with bigfile.BigFileMPI(comm=self.comm, filename=output, create=True) as ff:
            try:
                bb = ff.open(header)
            except:
                bb = ff.create(header)
            with bb :
                for key in self.attrs:
                    bb.attrs[key] = self.attrs[key]

            for column, dataset in zip(columns, datasets):
                c = self[column]

                with ff.create_from_array(dataset, c) as bb:
                    if hasattr(c, 'attrs'): 
                        for key in c.attrs:
                            bb.attrs[key] = c.attrs[key]

    def __setitem__(self, col, value):
        import dask.array as da
        assert len(value) == self.size
        self._overrides[col] = self.make_column(value)

    def read(self, columns):
        """
        Return the requested columns as dask arrays

        Currently, this returns a dask array holding the total amount
        of data for each rank, divided equally amongst the available ranks
        """
        missing = set(columns) - set(self.columns)
        if len(missing) > 0:
            raise ValueError("self does not contain columns: %s" %str(missing))

        return [self[col] for col in columns]
