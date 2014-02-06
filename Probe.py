__author__ = "Mikael Mortensen <mikaem@math.uio.no>"
__date__ = "2011-12-19"
__copyright__ = "Copyright (C) 2011 " + __author__
__license__  = "GNU Lesser GPL version 3 or any later version"
"""
This module contains functionality for efficiently probing a Function many times. 
"""
from dolfin import *
from numpy import zeros, array, squeeze, reshape 
import os, inspect
from mpi4py.MPI import COMM_WORLD as comm

# Compile Probe C++ code
def strip_essential_code(filenames):
    code = ""
    for name in filenames:
        f = open(name, 'r').read()
        code += f[f.find("namespace dolfin\n{\n"):f.find("#endif")]
    return code

dolfin_folder = os.path.abspath(os.path.join(inspect.getfile(inspect.currentframe()), "../Probe"))
sources = ["Probe.cpp", "Probes.cpp", "StatisticsProbe.cpp", "StatisticsProbes.cpp"]
headers = map(lambda x: os.path.join(dolfin_folder, x), ['Probe.h', 'Probes.h', 'StatisticsProbe.h', 'StatisticsProbes.h'])
code = strip_essential_code(headers)
compiled_module = compile_extension_module(code=code, source_directory=os.path.abspath(dolfin_folder),
                                           sources=sources, include_dirs=[".", os.path.abspath(dolfin_folder)])

# Give the compiled classes some additional pythonic functionality
class Probe(compiled_module.Probe):
    
    def __call__(self, *args):
        return self.eval(*args)

    def __len__(self):
        return self.value_size()
    
    def __getitem__(self, i):
        return self.get_probe_at_snapshot(i)

class Probes(compiled_module.Probes):

    def __call__(self, *args):
        return self.eval(*args)
        
    def __len__(self):
        return self.local_size()

    def __iter__(self): 
        self.i = 0
        return self

    def __getitem__(self, i):
        return self.get_probe_id(i), self.get_probe(i)

    def next(self):
        try:
            p =  self[self.i]
        except:
            raise StopIteration
        self.i += 1
        return p    

    def array(self, N=None, filename=None, component=None, root=0):
        """Dump data to numpy format on root processor for all or one snapshot."""
        is_root = comm.Get_rank() == root
        size = self.get_total_number_probes() if is_root else len(self)
        comp = self.value_size() if component is None else 1
        if not N is None:
            z  = zeros((size, comp))
        else:
            z  = zeros((size, comp, self.number_of_evaluations()))
        
        # Get all values
        if len(self) > 0: 
            if not N is None:
                for k in range(comp):
                    if is_root:
                        ids = self.get_probe_ids()
                        z[ids, k] = self.get_probes_component_and_snapshot(k, N)
                    else:
                        z[:, k] = self.get_probes_component_and_snapshot(k, N)
            else:                
                for i, (index, probe) in enumerate(self):
                    j = index if is_root else i
                    if not N is None:
                        z[j, :] = probe.get_probe_at_snapshot(N)
                    else:
                        for k in range(self.value_size()):
                            z[j, k, :] = probe.get_probe_sub(k)
                        
        # Collect values on root
        recvfrom = comm.gather(len(self), root=root)
        if is_root:
            for j, k in enumerate(recvfrom):                
                if comm.Get_rank() != j:
                    ids = comm.recv(source=j, tag=101)
                    z0 = comm.recv(source=j, tag=102)
                    z[ids, :] = z0[:, :]
        else:
            ids = self.get_probe_ids()
            comm.send(ids, dest=root, tag=101)
            comm.send(z, dest=root, tag=102)
            
        if is_root:
            if filename:
                if not N is None:
                    z.dump(filename+"_snapshot_"+str(N)+".probes")
                else:
                    z.dump(filename+"_all.probes")
            return squeeze(z)

class StatisticsProbe(compiled_module.StatisticsProbe):
    
    def __call__(self, *args):
        return self.eval(*args)

    def __len__(self):
        return self.value_size()
    
    def __getitem__(self, i):
        assert(i < 2)
        return self.get_probe_at_snapshot(i)

class StatisticsProbes(compiled_module.StatisticsProbes):

    def __call__(self, *args):
        return self.eval(*args)
        
    def __len__(self):
        return self.local_size()

    def __iter__(self): 
        self.i = 0
        return self

    def __getitem__(self, i):
        return self.get_probe_id(i), self.get_probe(i)

    def next(self):
        try:
            p = self[self.i]
        except:
            raise StopIteration
        self.i += 1
        return p   
        
    def array(self, N=0, filename=None, component=None, root=0):
        """Dump data to numpy format on root processor."""
        assert(N == 0 or N == 1)
        is_root = comm.Get_rank() == root
        size = self.get_total_number_probes() if is_root else len(self)
        comp = self.value_size() if component is None else 1        
        z  = zeros((size, comp))
        
        # Retrieve all values
        if len(self) > 0: 
            for k in range(comp):
                if is_root:
                    ids = self.get_probe_ids()
                    z[ids, k] = self.get_probes_component_and_snapshot(k, N)
                else:
                    z[:, k] = self.get_probes_component_and_snapshot(k, N)
                     
        # Collect on root
        recvfrom = comm.gather(len(self), root=root)
        if is_root:
            for j, k in enumerate(recvfrom):                
                if comm.Get_rank() != j:
                    ids = comm.recv(source=j, tag=101)
                    z0 = comm.recv(source=j, tag=102)
                    z[ids, :] = z0[:, :]
        else:
            ids = self.get_probe_ids()
            comm.send(ids, dest=root, tag=101)
            comm.send(z, dest=root, tag=102)
            
        if is_root:
            if filename:
                z.dump(filename+"_statistics.probes")
            return squeeze(z)


def interpolate_nonmatching_mesh_python(u0, V):
    """interpolate from Function u0 in V0 to a Function in V."""
    V0 = u0.function_space()
    mesh1 = V.mesh()
    gdim = mesh1.geometry().dim()
    owner_range = V.dofmap().ownership_range()
    u = Function(V)
    xs = V.dofmap().tabulate_all_coordinates(mesh1).reshape((-1, gdim))
    
    def extract_dof_component_map(dof_component_map, VV, comp):
        
        if VV.num_sub_spaces() == 0:
            ss = VV.dofmap().collapse(VV.mesh())
            comp[0] += 1 
            for val in ss[1].values():
                dof_component_map[val] = comp[0]
            
        else:
            for i in range(VV.num_sub_spaces()):
                Vs = VV.extract_sub_space(array([i], 'I'))
                extract_dof_component_map(dof_component_map, Vs, comp)

    # Create a map from global dof to component in Mixed space
    dof_component_map = {}
    comp = array([-1], 'I')
    extract_dof_component_map(dof_component_map, V, comp)
    
    # Now find the values of all these locations using Probes
    # Do this sequentially to save memory    
    dd = zeros(u.vector().local_size())
    for i in range(MPI.num_processes()):
        # Put all locations of this mesh on other processes
        xb = comm.bcast(xs, root=i)        
        # Probe these locations and store result in u
        probes = Probes(xb.flatten(), V0)
        probes(u0)  # evaluate the probes
        data = probes.array(N=0, root=i)
        probes.clear()
        if i == comm.Get_rank():
            if V.num_sub_spaces() < 1:
                u.vector().set_local(squeeze(data))
            else:
                for j in range(data.shape[0]):
                    dof = j + owner_range[0]
                    dd[j] = data[j, dof_component_map[dof]]
                u.vector().set_local(dd)
        
    return u

    
# Test the probe functions:
if __name__=='__main__':
    #set_log_active(False)
    set_log_level(20)

    mesh = UnitCubeMesh(16, 16, 16)
    #mesh = UnitSquareMesh(10, 10)
    V = FunctionSpace(mesh, 'CG', 1)
    Vv = VectorFunctionSpace(mesh, 'CG', 1)
    W = V * Vv
    
    # Just create some random data to be used for probing
    w0 = interpolate(Expression(('x[0]', 'x[1]', 'x[2]', 'x[1]*x[2]')), W)
        
    w0.update()
    
    x = array([[1.5, 0.5, 0.5], [0.2, 0.3, 0.4], [0.8, 0.9, 1.0]])
    p = Probes(x.flatten(), W)
    x = x*0.9 
    p.add_positions(x.flatten(), W)
    for i in range(6):
        p(w0)
        
    print p.array(2, "testarray")         # dump snapshot 2
    print p.array(filename="testarray")   # dump all snapshots
    print p.dump("testarray")

    interactive()
    
