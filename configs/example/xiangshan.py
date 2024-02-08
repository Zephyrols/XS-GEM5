# Copyright (c) 2010-2013, 2016, 2019-2020 ARM Limited
# Copyright (c) 2020 Barkhausen Institut
# All rights reserved.
#
# The license below extends only to copyright in the software and shall
# not be construed as granting a license to any other intellectual
# property including but not limited to intellectual property relating
# to a hardware implementation of the functionality of the software
# licensed hereunder.  You may use the software subject to the license
# terms below provided that you ensure that this notice is replicated
# unmodified and in its entirety in all distributions of the software,
# modified or unmodified, in source code or in binary form.
#
# Copyright (c) 2012-2014 Mark D. Hill and David A. Wood
# Copyright (c) 2009-2011 Advanced Micro Devices, Inc.
# Copyright (c) 2006-2007 The Regents of The University of Michigan
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import argparse
import sys

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath, fatal, warn
from m5.util.fdthelper import *

addToPath('../')

from ruby import Ruby

from common.FSConfig import *
from common.SysPaths import *
from common.Benchmarks import *
from common import Simulation
from common import CacheConfig
from common import CpuConfig
from common import MemConfig
from common import ObjectList
from common.Caches import *
from common import Options

def build_test_system(np):
    assert buildEnv['TARGET_ISA'] == "riscv"
    test_sys = makeBareMetalXiangshanSystem(test_mem_mode, SysConfig(mem=args.mem_size), None)
    test_sys.xiangshan_system = True
     # Set the cache line size for the entire system
    test_sys.cache_line_size = args.cacheline_size

    # Create a top-level voltage domain
    test_sys.voltage_domain = VoltageDomain(voltage = args.sys_voltage)

    # Create a source clock for the system and set the clock period
    test_sys.clk_domain = SrcClockDomain(clock =  args.sys_clock,
            voltage_domain = test_sys.voltage_domain)

    # Create a CPU voltage domain
    test_sys.cpu_voltage_domain = VoltageDomain()

    # Create a source clock for the CPUs and set the clock period
    test_sys.cpu_clk_domain = SrcClockDomain(clock = args.cpu_clock,
                                             voltage_domain =
                                             test_sys.cpu_voltage_domain)

    if args.generic_rv_cpt is not None :
        test_sys.workload.bootloader = ''
        test_sys.workload.xiangshan_cpt = True
        if args.raw_cpt:
            test_sys.map_to_raw_cpt = True
            print('Using raw bbl', args.kernel)
            test_sys.workload.raw_bootloader = True

    # For now, assign all the CPUs to the same clock domain
    test_sys.cpu = [TestCPUClass(clk_domain=test_sys.cpu_clk_domain, cpu_id=i)
                    for i in range(np)]
    for cpu in test_sys.cpu:
        cpu.mmu.pma_checker = PMAChecker(
            uncacheable=[AddrRange(0, size=0x80000000)])

    # configure BP
    for i in range(np):
        if args.bp_type is None or args.bp_type == 'DecoupledBPUWithFTB':
            enable_bp_db = len(args.enable_bp_db) > 1
            if enable_bp_db:
                bp_db_switches = args.enable_bp_db[1] + ['basic']
                print("BP db switches:", bp_db_switches)
            else:
                bp_db_switches = []

            test_sys.cpu[i].branchPred = DecoupledBPUWithFTB(
                                            bpDBSwitches=bp_db_switches,
                                            enableLoopBuffer=args.enable_loop_buffer,
                                            enableLoopPredictor=args.enable_loop_predictor,
                                            enableJumpAheadPredictor=args.enable_jump_ahead_predictor
                                            )
            test_sys.cpu[i].branchPred.isDumpMisspredPC = True
        else:
            test_sys.cpu[i].branchPred = ObjectList.bp_list.get(args.bp_type)

        if args.indirect_bp_type:
            IndirectBPClass = ObjectList.indirect_bp_list.get(
                args.indirect_bp_type)
            test_sys.cpu[i].branchPred.indirectBranchPred = \
                    IndirectBPClass()

    # configure memory related
    if args.mem_type == 'DRAMsim3':
        assert args.dramsim3_ini is not None

    if hasattr(args, 'ruby') and args.ruby:
        bootmem = getattr(test_sys, '_bootmem', None)
        Ruby.create_system(args, True, test_sys, test_sys.iobus,
                           test_sys._dma_ports, bootmem)

        # Create a seperate clock domain for Ruby
        test_sys.ruby.clk_domain = SrcClockDomain(clock = args.ruby_clock,
                                        voltage_domain = test_sys.voltage_domain)

        # Connect the ruby io port to the PIO bus,
        # assuming that there is just one such port.
        test_sys.iobus.mem_side_ports = test_sys.ruby._io_port.in_ports

        for (i, cpu) in enumerate(test_sys.cpu):
            # Tie the cpu ports to the correct ruby system ports
            cpu.clk_domain = test_sys.cpu_clk_domain
            cpu.createThreads()
            print("Create threads for test sys cpu ({})".format(type(cpu)))
            cpu.createInterruptController()

            test_sys.ruby._cpu_ports[i].connectCpuPorts(cpu)

    else:
        if args.caches or args.l2cache:
            # By default the IOCache runs at the system clock
            test_sys.iocache = IOCache(addr_ranges = test_sys.mem_ranges)
            test_sys.iocache.cpu_side = test_sys.iobus.mem_side_ports
            test_sys.iocache.mem_side = test_sys.membus.cpu_side_ports
        elif not args.external_memory_system:
            test_sys.iobridge = Bridge(delay='50ns', ranges = test_sys.mem_ranges)
            test_sys.iobridge.cpu_side_port = test_sys.iobus.mem_side_ports
            test_sys.iobridge.mem_side_port = test_sys.membus.cpu_side_ports

        for i in range(np):
            test_sys.cpu[i].createThreads()
            print("Create threads for test sys cpu ({})".format(type(test_sys.cpu[i])))

        for opt in ['caches', 'l2cache', 'l1_to_l2_pf_hint']:
            if hasattr(args, opt) and not getattr(args, opt):
                setattr(args, opt, True)

        if not args.no_l3cache:
            for opt in ['l3cache', 'l2_to_l3_pf_hint']:
                if hasattr(args, opt) and not getattr(args, opt):
                    setattr(args, opt, True)

        if args.xiangshan_ecore and args.no_l3cache:
            args.l2_size = '4MB'

        CacheConfig.config_cache(args, test_sys)

        MemConfig.config_mem(args, test_sys)

    if args.mmc_img:
        for mmc, cpu in zip(test_sys.mmcs, test_sys.cpu):
            mmc.cpt_bin_path = args.mmc_cptbin
            mmc.img_path = args.mmc_img
            cpu.nemuSDCptBin = mmc.cpt_bin_path
            cpu.nemuSDimg = mmc.img_path

    CpuConfig.config_difftest(TestCPUClass, test_sys.cpu, args)

    # config arch db
    if args.enable_arch_db:
        test_sys.arch_db = ArchDBer(arch_db_file=args.arch_db_file)
        test_sys.arch_db.dump_from_start = args.arch_db_fromstart
        test_sys.arch_db.enable_rolling = args.enable_rolling
        test_sys.arch_db.dump_l1_pf_trace = False
        test_sys.arch_db.dump_mem_trace = False
        test_sys.arch_db.dump_l1_evict_trace = False
        test_sys.arch_db.dump_l2_evict_trace = False
        test_sys.arch_db.dump_l3_evict_trace = False
        test_sys.arch_db.dump_l1_miss_trace = False
        test_sys.arch_db.dump_bop_train_trace = False
        test_sys.arch_db.dump_sms_train_trace = False
        test_sys.arch_db.table_cmds = [
            "CREATE TABLE L1MissTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "PC INT NOT NULL," \
            "SOURCE INT NOT NULL," \
            "PADDR INT NOT NULL," \
            "VADDR INT NOT NULL," \
            "STAMP INT NOT NULL," \
            "SITE TEXT);"
            ,
            "CREATE TABLE CacheEvictTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "Tick INT NOT NULL," \
            "PADDR INT NOT NULL," \
            "STAMP INT NOT NULL," \
            "Level INT NOT NULL," \
            "SITE TEXT);"
            ,
            "CREATE TABLE MemTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "Tick INT NOT NULL," \
            "IsLoad BOOL NOT NULL," \
            "PC INT NOT NULL," \
            "VADDR INT NOT NULL," \
            "PADDR INT NOT NULL," \
            "Issued INT NOT NULL," \
            "Translated INT NOT NULL," \
            "Completed INT NOT NULL," \
            "Committed INT NOT NULL," \
            "Writenback INT NOT NULL," \
            "PFSrc INT NOT NULL," \
            "SITE TEXT);"
            ,
            "CREATE TABLE L1PFTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "Tick INT NOT NULL," \
            "TriggerPC INT NOT NULL," \
            "TriggerVAddr INT NOT NULL," \
            "PFVAddr INT NOT NULL," \
            "PFSrc INT NOT NULL," \
            "SITE TEXT);"
            ,

            "CREATE TABLE BOPTrainTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "Tick INT NOT NULL," \
            "OldAddr INT NOT NULL," \
            "CurAddr INT NOT NULL," \
            "Offset INT NOT NULL," \
            "Score INT NOT NULL," \
            "Miss BOOL NOT NULL," \
            "SITE TEXT);"

            "CREATE TABLE SMSTrainTrace(" \
            "ID INTEGER PRIMARY KEY AUTOINCREMENT," \
            "Tick INT NOT NULL," \
            "OldAddr INT NOT NULL," \
            "CurAddr INT NOT NULL," \
            "TriggerOffset INT NOT NULL," \
            "Conf INT NOT NULL," \
            "Miss BOOL NOT NULL," \
            "SITE TEXT);"
        ]

    # config debug trace
    for i in range(np):
        if args.dump_commit:
            test_sys.cpu[i].dump_commit = True
            test_sys.cpu[i].dump_start = args.dump_start
        else:
            test_sys.cpu[i].dump_commit = False
            test_sys.cpu[i].dump_start = 0

    return test_sys

# Add args
parser = argparse.ArgumentParser()
Options.addCommonOptions(parser, configure_xiangshan=True)
Options.addXiangshanFSOptions(parser)

# Add the ruby specific and protocol specific args
if '--ruby' in sys.argv:
    Ruby.define_options(parser)

args = parser.parse_args()

args.xiangshan_system = True

assert not args.external_memory_system

test_mem_mode = 'timing'

# override cpu class and clock
if args.xiangshan_ecore:
    TestCPUClass = XiangshanECore
    FutureClass = None
    args.cpu_clock = '2.4GHz'
else:
    TestCPUClass = XiangshanCore
    FutureClass = None

# Match the memories with the CPUs, based on the options for the test system
TestMemClass = Simulation.setMemClass(args)

np = args.num_cpus

test_sys = build_test_system(np)

if args.generic_rv_cpt is not None:
    assert(buildEnv['TARGET_ISA'] == "riscv")
    test_sys.restore_from_gcpt = True
    test_sys.gcpt_file = args.generic_rv_cpt
    test_sys.gcpt_restorer_file = args.gcpt_restorer

if args.enable_riscv_vector:
    print("Enable riscv vector difftest, need riscv vector-supported gcpt restore and diff-ref-so")
    test_sys.enable_riscv_vector = True
    for cpu in test_sys.cpu:
        cpu.enable_riscv_vector = True


root = Root(full_system=True, system=test_sys)

Simulation.run_vanilla(args, root, test_sys, FutureClass)
