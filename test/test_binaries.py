import os
import math
import pytest
import parameterized
import itertools
import pyomo.environ as pyo
import pyomo.common.unittest as unittest
from pyomo.contrib.sensitivity_toolbox.sens import sensitivity_calculation, _add_sensitivity_suffixes
from pyomo.contrib.sensitivity_toolbox.k_aug import InTempDir
from contextlib import nullcontext


# TODO: This directory will be wherever we download the binaries that we
# want to test, likely just in the current working directory.
if "IDAES_DIR" in os.environ:
    IDAES_DIR = os.environ["IDAES_DIR"]
else:
    # Note that this directory is specific to mac and linux
    IDAES_DIR = os.path.join(os.environ["HOME"], ".idaes")
ipopts_to_test = [
    ("ipopt", os.path.join(IDAES_DIR, "bin", "ipopt")),
    ("ipopt_l1", os.path.join(IDAES_DIR, "bin", "ipopt_l1")),
    #("cyipopt", None),
]
ipopt_options_to_test = [
    ("default", {}),
    ("mumps", {"print_user_options": "yes", "linear_solver": "mumps"}),
    ("ma27", {"print_user_options": "yes", "linear_solver": "ma27"}),
    ("ma57", {"print_user_options": "yes", "linear_solver": "ma57"}),
    ("ma57_metis", {"print_user_options": "yes", "linear_solver": "ma57", "ma57_pivot_order": 4}),
]
sensitivity_solvers = [
    ("ipopt", "k_aug", "dot_sens"),
    ("ipopt_sens", "ipopt_sens", None),
    ("ipopt_sens_l1", "ipopt_sense_l1", None),
]
TEE = True
ipopt_test_data = list(itertools.product(ipopts_to_test, ipopt_options_to_test))
ipopt_test_data = [
    (f"{ipoptname}_{optname}", ipoptexe, options)
    for (ipoptname, ipoptexe), (optname, options) in ipopt_test_data
]


def _test_ipopt_with_options(name, exe, options):
    m = pyo.ConcreteModel()
    m.x = pyo.Var([1,2], initialize=1.5)
    m.con = pyo.Constraint(expr=m.x[1]*m.x[2] == 0.5)
    m.obj = pyo.Objective(expr=m.x[1]**2 + 2*m.x[2]**2)

    if exe is None:
        solver = pyo.SolverFactory(name, options=options)
    else:
        solver = pyo.SolverFactory(name, executable=exe, options=options)

    if "ipopt_l1" in name:
        # Run this in a temp dir so we don't pollute the working directory with
        # ipopt_l1's files. See https://github.com/IDAES/idaes-ext/issues/275
        context = InTempDir()
    else:
        context = nullcontext()
    with context:
        solver.solve(m, tee=TEE)

    target_sol = [("x[1]", 0.840896415), ("x[2]", 0.594603557)]
    assert all(
        math.isclose(m.find_component(name).value, val, abs_tol=1e-7)
        for name, val in target_sol
    )


class TestIpopt(unittest.TestCase):

    @parameterized.parameterized.expand(ipopt_test_data)
    def test_ipopt(self, solver_name, exe, options):
        _test_ipopt_with_options(solver_name, exe, options)


class TestBonmin:

    exe = os.path.join(IDAES_DIR, "bin", "bonmin")

    def _make_model(self):
        m = pyo.ConcreteModel()
        m.x = pyo.Var([1, 2], initialize=1.5)
        m.y = pyo.Var(domain=pyo.PositiveIntegers)
        m.con = pyo.Constraint(expr=m.x[1]*m.x[2] == m.y)
        m.obj = pyo.Objective(expr=m.x[1]**2 + 2*m.x[2]**2)
        return m

    def test_bonmin_default(self):
        m = self._make_model()
        solver = pyo.SolverFactory("bonmin", executable=self.exe)
        solver.solve(m, tee=TEE)

        assert math.isclose(m.y.value, 1.0, abs_tol=1e-7)
        assert math.isclose(m.x[1].value, 1.18920710, abs_tol=1e-7)
        assert math.isclose(m.x[2].value, 0.84089641, abs_tol=1e-7)


class TestCouenne:

    exe = os.path.join(IDAES_DIR, "bin", "couenne")

    def _make_model(self):
        m = pyo.ConcreteModel()
        m.x = pyo.Var([1, 2], initialize=1.5)
        m.y = pyo.Var(domain=pyo.PositiveIntegers)
        m.con = pyo.Constraint(expr=m.x[1]*m.x[2] == m.y)
        m.obj = pyo.Objective(expr=(m.x[1] + 0.01)**2 + 2*(m.x[2] + 0.01)**2)
        return m

    def test_couenne_default(self):
        m = self._make_model()
        solver = pyo.SolverFactory("couenne", executable=self.exe)
        solver.solve(m, tee=TEE)

        assert math.isclose(m.y.value, 1.0, abs_tol=1e-7)
        assert math.isclose(m.x[1].value, -1.18816674, abs_tol=1e-7)
        assert math.isclose(m.x[2].value, -0.84163270, abs_tol=1e-7)


def _test_sensitivity(
    solver_name,
    solver_exe,
    sens_name,
    sens_exe,
    update_name,
    update_exe,
):
    m = pyo.ConcreteModel()
    m.x = pyo.Var([1,2], initialize=1.5)
    m.p = pyo.Param(mutable=True, initialize=0.5)
    m.con = pyo.Constraint(expr=m.x[1]*m.x[2] == m.p)
    m.obj = pyo.Objective(expr=m.x[1]**2 + 2*m.x[2]**2)

    if solver_exe is None:
        solver = pyo.SolverFactory(solver_name)
    else:
        solver = pyo.SolverFactory(solver_name, executable=solver_exe)
    solver.solve(m, tee=TEE, keepfiles=True)
    if sens_name == "k_aug":
        sensitivity_executable = (sens_exe, update_exe)
    else:
        sensitivity_executable = sens_exe
    sensitivity_calculation(
        sens_name,
        m,
        [m.p],
        [0.7],
        cloneModel=False,
        tee=TEE,
        sensitivity_executable=sensitivity_executable,
        solver_executable=solver_exe,
    )
    solution = {"x[1]": 0.95, "x[2]": 0.75}
    if sens_name == "sipopt":
        # sipopt puts the perturbed solution in suffixes
        for var, val in solution.items():
            # Use a loose tolerance because methods seem to give different solutions...
            assert math.isclose(
                m.sens_sol_state_1[m.find_component(var)], val, abs_tol=1e-1
            )
    elif sens_name == "k_aug":
        # K_aug puts the perturbed solution back in the model
        for var, val in solution.items():
            # Use a loose tolerance because methods seem to give different solutions...
            assert math.isclose(m.find_component(var).value, val, abs_tol=1e-1)


class TestSensitivity:

    ipopt_exe = os.path.join(IDAES_DIR, "bin", "ipopt")
    sipopt_exe = os.path.join(IDAES_DIR, "bin", "ipopt_sens")
    k_aug_exe = os.path.join(IDAES_DIR, "bin", "k_aug")
    dot_sens_exe = os.path.join(IDAES_DIR, "bin", "dot_sens")

    def test_k_aug(self):
        _test_sensitivity(
            "ipopt",
            self.ipopt_exe,
            "k_aug",
            self.k_aug_exe,
            "dot_sens",
            self.dot_sens_exe,
        )

    def test_sipopt(self):
        _test_sensitivity(
            "ipopt", self.ipopt_exe, "sipopt", self.sipopt_exe, None, None
        )


if __name__ == "__main__":
    pytest.main([__file__])
