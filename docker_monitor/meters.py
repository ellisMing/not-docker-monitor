# coding=utf-8
# Copyright 2016 NUTC i.m.a.c.
# All Rights Reserved

import logging
import commands
import time
import multiprocessing
from threading import Thread

LOG = logging.getLogger("collect.meters")

SYS_CORE = multiprocessing.cpu_count()
SYS_CPU_CMD = "awk '/cpu / {for(i=2;i<=NF;i++) t+=$i; print t; t=0}' /proc/stat"
SYS_MEM_CMD = "free -m | awk '{print $2}' | sed -n '2p'"


class Meters(Thread):
    """
    Docker CGroup meters class, this class is define all meters
    """

    CPU = 0x01
    MEMORY = 0x02

    _CGROUP_PATHS = {
        CPU: "/sys/fs/cgroup/cpuacct/docker",
        MEMORY: "/sys/fs/cgroup/memory/docker",
    }

    _CGROUP_FILE = {
        CPU: "cpuacct.usage",
        MEMORY: "memory.usage_in_bytes"
    }

    def __init__(self, func, **kwargs):
        super(Meters, self).__init__()

        self.kwargs = kwargs

        window_time = self.kwargs['window_time']
        self.window_time = window_time if window_time else 0.5
        self.container_ids = None
        self.callback = func

        self.f_usage = dict()
        self.l_usage = dict()

    def get_container_ids(self):
        ids = commands.getoutput("docker ps -q --no-trunc")
        if ids != '':
            return ids.split("\n")
        else:
            return []

    def _get_usages(self):
        usages = dict()
        sys_cpu_usage = int(commands.getoutput(SYS_CPU_CMD))
        sys_mem_total = int(commands.getoutput(SYS_MEM_CMD))

        for container_id in self.container_ids:
            usages[container_id] = {}
            for (key, path_v), (_, file_v) in zip(
                    Meters._CGROUP_PATHS.items(),
                    Meters._CGROUP_FILE.items()
            ):
                path = "{path}/{id}/{file_name}".format(
                    path=path_v,
                    id=container_id,
                    file_name=file_v,
                )
                with open(path, 'r') as f:
                    if key == Meters.CPU:
                        usages[container_id].update({
                            'sys_cpu': sys_cpu_usage,
                            'cgroup_cpu': int(f.read()),
                        })
                    else:
                        usages[container_id].update({
                            'cgroup_memory': int(f.read()),
                            'total_memory': sys_mem_total,
                            'free_memory': sys_mem_total - int(f.read()),
                        })

        return usages

    def calc_cpu_usage(self, first, last):
        sys_diff = (last['sys_cpu'] != first['sys_cpu'])
        cgroup_diff = (last['cgroup_cpu'] != first['cgroup_cpu'])

        if sys_diff and cgroup_diff:
            cpu_total = last['sys_cpu'] - first['sys_cpu']
            cgroup_total = last['cgroup_cpu'] - first['cgroup_cpu']
            return cgroup_total * SYS_CORE / cpu_total * 100 / float(10000000)
        else:
            return None

    def calc_mem_total(self, last):
        return last['total_memory']

    def calc_mem_usage(self, last):
        return last['cgroup_memory'] / float(1000000)

    def calc_mem_free(self, last):
        return last['free_memory']

    def get_usage_rate(self):
        rates = dict()
        for container_id in self.container_ids:
            first = self.f_usage[container_id]
            last = self.l_usage[container_id]

            mem_usage = self.calc_mem_usage(last)
            mem_total = self.calc_mem_total(last)
            mem_free = self.calc_mem_free(last)
            cpu_rate = self.calc_cpu_usage(first, last)
            if cpu_rate:
                self.f_usage[container_id] = self.l_usage[container_id]
                rates[container_id[0:12]] = {'cpu': cpu_rate, 'memory': mem_usage, 'mem_total': mem_total, 'mem_free': mem_free}

        return rates

    def live_container(self, rates):
        short_ids = [long_id[0:12] for long_id in self.container_ids]
        unlive_ids = list(set(rates.keys()) - set(short_ids))
        for container_id in unlive_ids:
            try:
                del rates[container_id]
            except KeyError:
                pass

    def run(self):
        callback_rates = dict()
        while True:
            try:
                self.container_ids = self.get_container_ids()
                if len(self.container_ids) > 0:
                    usages = self._get_usages()

                    if len(set(self.f_usage.keys()) ^ set(usages.keys())) != 0:
                        self.f_usage = usages
                    else:
                        self.l_usage = usages
                        rates = self.get_usage_rate()
                        if rates:
                            callback_rates.update(rates)
                            self.live_container(callback_rates)
                            self.callback(callback_rates)

            except Exception as e:
                LOG.error("%s" % (e.__str__()))

            time.sleep(self.window_time)
