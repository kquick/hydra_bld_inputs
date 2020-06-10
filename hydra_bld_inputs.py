#! /usr/bin/env nix-shell
#! nix-shell -i "python3.7 -u" -p git swiProlog "python37.withPackages(pp: with pp; [ thespian setproctitle attrs requests ])"

import attr
import requests
import json
from KVITable import KVITable
from typing import Any, Dict, List, Tuple, Union


class HydraBuilder(object):
    def __init__(self, builder_url: str):
        self.url_base = builder_url[:-1] if builder_url[-1] == '/' else builder_url
        self._cache = {}

    def _get(self, *parts):
        if parts in self._cache:
            return self._cache[parts]
        url = '/'.join([self.url_base] + list(parts))
        # print('---> GET',url)
        r = requests.get(url,
                         headers={'Content-type': 'application/json'})
        r.raise_for_status()
        # print(r)
        # print(r.json())
        self._cache[parts] = r.json()
        return r.json()

    def fetch_eval_builds_and_inputs(self, eval_id: str) -> Tuple[List[int],
                                                                  Dict[str, Union[str, int]]]:
        rj = self._get('eval', eval_id)
        return rj['builds'], rj['jobsetevalinputs']


    def fetch_build(self, buildnum: str) -> Dict[str, Any]:
        return self._get('build', buildnum)

    def fetch_jobset(self, project_name: str, jobset_name: str) -> Dict[str, Any]:
        return self._get('jobset', project_name, jobset_name)


class HydraProject(object):
    def __init__(self, builder: HydraBuilder, project_name: str):
        self.builder = builder
        self.project_name = project_name

    @property
    def name(self): return self.project_name


class HydraJobset(object):
    def __init__(self, builder: HydraBuilder, project: HydraProject, jobset_name: str):
        self.builder = builder
        self.project = project
        self.jobset_name = jobset_name
        self._inputs = None

    @property
    def name(self): return self.jobset_name

    @property
    def inputs(self):
        if not self._inputs:
            self._fetch()
        return self._inputs

    def _fetch(self):
        r = self.builder.fetch_jobset(self.project.name, self.name)
        self._inputs = { inp: r['jobsetinputs'][inp]['jobsetinputalts'][0]
                         # n.b. never seen any entry other than 'jobsetinputalts'
                         # n.b. never seen more than one array entry
                         for inp in r['jobsetinputs'] }


class HydraEval(object):
    def __init__(self, builder: HydraBuilder, eval_id: int):
        self.builder = builder
        self.eval_id = eval_id
        self._project = None
        self._jobset = None
        self._inputs = None
        self._builds = None
        self._input_blds : Dict[str, HydraBuild] = {}

    @property
    def project(self):
        if self._project: return self._project
        if self._builds:
            self._project = self._builds[0].project
            return self._project
        raise RuntimeError('need to fetch project_name')

    @property
    def jobset(self):
        if self._jobset: return self._jobset
        if self._builds:
            self._jobset = self._builds[0].jobset
            return self._jobset
        raise RuntimeError('need to fetch jobset_name')

    @property
    def inputs(self):
        '''Returns dict { "inp_name": { "is": "...", ..."value": VAL } }'''
        if self._inputs: return self._inputs
        self._fetch_inputs()
        return self._inputs

    def _fetch_inputs(self):
        blds, inps = self.builder.fetch_eval_builds_and_inputs(self.eval_id)
        if not inps:
            raise RuntimeError('This very strange project has no inputs!')
        if not blds:
            raise RuntimeError('No builds yet to obtain inputs for')
        self._builds = [ HydraBuild(self.builder, str(bld_id)) for bld_id in blds ]
        self._inputs = { inp : self._input(inp, inps[inp]) for inp in inps }

    def _input(self, name, vals):
        if 'type' not in vals:
            print("????? parse",name,'with',vals)
        return { 'string': self._string_input,
                 'boolean': self._boolean_input,
                 'build': self._build_input,
                 'git': self._git_input,
                 'path': self._path_input,
        }[vals['type']](name, vals)

    def _string_input(self, name, vals):
        return { 'is': 'str', 'value': vals['value'] }

    def _path_input(self, name, vals):
        return { 'is': 'path', 'value': vals['value'] }

    def _boolean_input(self, name, vals):
        return { 'is': 'bool', 'value': vals['value'] }

    def _git_input(self, name, vals):
        return { 'is': 'git', 'uri': vals['uri'], 'rev': vals['revision'] }

    def _build_input(self, name, vals):
        buildid = vals['dependency']
        if buildid not in self._input_blds:
            self._input_blds[buildid] = HydraBuild(self.builder, str(buildid))
        bld = self._input_blds[buildid]
        # inputs[name] is "project:jobset:inp"; just want "inp"
        dep_output = self.jobset.inputs[name].split(':')[-1]
        # Note: Briareus project-input assumption: getting an input
        # means the "input-src" input from the dependency
        briareus_inp = lambda ref: ref + "-src"
        # General assumption: the input name corresponding to the job
        # name is what is important
        inpref = dep_output

        # Prioritize the briareus generated input
        result = bld.latest_eval.inputs.get(briareus_inp(dep_output), None)
        if result: return result

        # Else use the input named the same as the output
        result = bld.latest_eval.inputs.get(inpref, None)
        if result: return result

        # Else just use the output path in the nix store
        return { 'is': 'path', 'value': bld.outputs['out']['path'] }


class HydraBuild(object):
    def __init__(self, builder: HydraBuilder, build_id: str):
        self.builder = builder
        self.build_id = build_id
        self._project = None
        self._jobset = None
        self._latest_eval = None
        self._outputs = None  # { "out": { "path": "/nix/store/...", ... }, ... }

    @property
    def project(self):
        if self._project: return self._project
        self._fetch()
        return self._project

    @property
    def jobset(self):
        if self._jobset: return self._jobset
        self._fetch()
        return self._jobset

    @property
    def latest_eval(self):
        if not self._latest_eval:
            self._fetch()
        return self._latest_eval

    @property
    def outputs(self):
        if self._outputs is None:
            self._fetch()
        return self._outputs

    def _fetch(self):
        r = self.builder.fetch_build(self.build_id)
        self._project = HydraProject(self.builder, r['project'])
        self._jobset = HydraJobset(self.builder, self._project, r['jobset'])
        self._latest_eval = HydraEval(self.builder, str(r['jobsetevals'][0]))
        self._outputs = r['buildoutputs']


def get_bld_inputs(builder_url, eval_id):
    builder = HydraBuilder(builder_url)
    eval = HydraEval(builder, eval_id)
    result = KVITable(['name','input'])
    for inp in sorted(eval.inputs):
        for key in eval.inputs[inp]:
            result.add(eval.inputs[inp][key],
                       name=inp, input='input',
                       key=key)
    print(result.render(colstack_at='input',
                        sort_vals=True,
                        row_repeat=False,
    ))


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print('Usage:',sys.argv[0],' hydra-url eval-num')
        sys.exit(1)
    get_bld_inputs(sys.argv[1], sys.argv[2])
