"""Manager for JupyterLite
"""
import sys
from logging import getLogger

import doit
from traitlets import Bool, Dict, Unicode, default

# See compatibility note on `group` keyword in
# https://docs.python.org/3/library/importlib.metadata.html#entry-points
if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points

from .config import LiteBuildConfig
from .constants import ADDON_ENTRYPOINT, HOOK_PARENTS, HOOKS, PHASES


class LiteManager(LiteBuildConfig):
    """a manager for building jupyterlite sites


    This primarily handles the business of mapping _addons_ to ``doit`` _tasks_,
    and then calling the ``doit`` API.
    """

    strict = Bool(
        True, help=("if `True`, stop the current workflow on the first error")
    ).tag(config=True)

    task_prefix = Unicode(
        default_value="",
        help="a prefix appended to all tasks generated by this manager",
    ).tag(config=True)

    # "private" traits (at least not configurable)
    _addons = Dict(
        help="""concrete addons that have named iterable methods of doit tasks"""
    )
    _doit_config = Dict(help="the DOIT_CONFIG for tasks")
    _doit_tasks = Dict(help="the doit task generators")

    def initialize(self):
        """perform one-time inialization of the manager"""
        self.log.debug("[lite] [addon] loading ...")
        self.log.debug(f"[lite] [addon] ... OK {len(self._addons)} addons")
        self.log.debug("[lite] [tasks] loading ...")
        self.log.debug(f"[lite] [tasks] ... OK {len(self._doit_tasks)} tasks")

    def doit_run(self, task, *args, raw=False):
        """run a subset of the doit command line"""
        loader = doit.cmd_base.ModuleTaskLoader(self._doit_tasks)
        config = dict(GLOBAL=self._doit_config)
        runner = doit.doit_cmd.DoitMain(task_loader=loader, extra_config=config)
        return runner.run([task, *args])

    @default("log")
    def _default_log(self):
        """prefer the parent application's log, or create a new one"""
        return self.parent.log if self.parent else getLogger(__name__)

    @default("_addons")
    def _default_addons(self):
        """initialize addons from entry_points

        if populated, ``disable_addons`` will be consulted
        """
        addons = {}
        for name, addon in self._addon_entry_points().items():
            if name in self.disable_addons:
                self.log.info(f"""[lite] [addon] [{name}] skipped by config""")
                continue
            self.log.debug(f"[lite] [addon] [{name}] load ...")
            try:
                addon_kwargs = dict(manager=self)
                if self._is_sys_prefix_ignored(name):
                    self.log.debug(f"[lite] [addon] [{name}] ... ignore sys prefix")
                    addon_kwargs.update(ignore_sys_prefix=True)
                addon_inst = addon.load()(**addon_kwargs)
                addons[name] = addon_inst
                for one in sorted(addon_inst.__all__):
                    self.log.debug(f"""[lite] [addon] [{name}] ... will {one}""")
            except Exception as err:
                self.log.warning(f"[lite] [addon] [{name}] FAIL", exc_info=err)
        return addons

    def _addon_entry_points(self):
        """Return modern entrypoints as a dict with sorted keys"""
        all_entry_points = {}
        for entry_point in entry_points(group=ADDON_ENTRYPOINT):
            name = entry_point.name
            if name in all_entry_points:
                self.log.warning(f"[lite] [addon] [{name}] already registered.")
                continue
            all_entry_points[name] = entry_point
        return dict(sorted(all_entry_points.items()))

    @default("_doit_config")
    def _default_doit_config(self):
        """our hardcoded ``DOIT_CONFIG``"""
        return {
            "dep_file": ".jupyterlite.doit.db",
            "backend": "sqlite3",
            "verbosity": 2,
        }

    @default("_doit_tasks")
    def _default_doit_tasks(self):
        """initialize the doit task generators"""
        tasks = {}
        prev_attr = None

        for hook in HOOKS:
            for phase in PHASES:
                if phase == "pre_":
                    if hook in HOOK_PARENTS:
                        prev_attr = f"""post_{HOOK_PARENTS[hook]}"""
                attr = f"{phase}{hook}"
                tasks[f"task_{self.task_prefix}{attr}"] = self._gather_tasks(
                    attr, prev_attr
                )
                prev_attr = attr

        return tasks

    def _gather_tasks(self, attr, prev_attr):
        """early up-front ``doit`` work"""

        def _gather():
            for name, addon in self._addons.items():
                if attr in addon.__all__:
                    try:
                        for task in getattr(addon, attr)(self):
                            patched_task = {**task}
                            patched_task[
                                "name"
                            ] = f"""{self.task_prefix}{name}:{task["name"]}"""
                            print(patched_task["name"])
                            yield patched_task
                    except Exception as err:
                        self.log.error(f"[lite] [{attr}] [{name}] [ERR] {err}")
                        if self.strict:
                            raise err

        if not prev_attr:
            return _gather

        @doit.create_after(f"{self.task_prefix}{prev_attr}")
        def _delayed_gather():
            for task in _gather():
                yield task

        return _delayed_gather

    def _is_sys_prefix_ignored(self, addon):
        ignore = self.ignore_sys_prefix
        return addon in ignore if isinstance(ignore, tuple) else ignore
