#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the actual probe IRQ policy and test mixed-card probe order."""
import argparse
from pathlib import Path
import os
import resource
import subprocess
import tempfile
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
a = parser.parse_args()
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
s = (a.source/"lib/sc0710-core.c").read_text()
start = s.index("\tif (irq_service && dev->board == SC0710_BOARD_ELGATO_CAMLINK_PRO)")
end = s.index("\n\t/* Card specific tweaks", start)
policy = s[start:end]
shim = r"""
#include <assert.h>
#include <stdio.h>
#include <stdbool.h>
#include <stddef.h>
#define SC0710_BOARD_ELGATO_CAMLINK_PRO 3
#define PCI_IRQ_MSI 1
#define KERN_INFO ""
#define KERN_WARNING ""
#define printk(...) ((void)printf(__VA_ARGS__))
struct device { int board; bool irq_requested, irq_service_active; const char *name; };
static unsigned irq_service;
static int allocations, requests, frees, alloc_result=1, request_result;
static void *sc0710_irq;
static int pci_alloc_irq_vectors(void *p,int a,int b,int c) {
 (void)p;(void)a;(void)b;(void)c;allocations++;return alloc_result;
}
static int pci_request_irq(void *p,int a,void *b,void *c,struct device *d,const char *e,const char *f) {
 (void)p;(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;requests++;return request_result;
}
static int pci_irq_vector(void *p,int a) {(void)p;(void)a;return 42;}
static void pci_free_irq_vectors(void *p) {(void)p;frees++;}
static void probe_policy(struct device *dev) { void *pci_dev=NULL;
"""
cases = r"""
}
int main(void) {
 struct device cam={.board=3,.name="cam"}, mk2={.board=1,.name="mk2"}, fourk={.board=2,.name="4kp"};
 irq_service=1;
 /* Cam Link may subsequently fail firmware loading; policy must stay local. */
 probe_policy(&cam);
 assert(irq_service==1 && allocations==0 && !cam.irq_service_active);
 probe_policy(&mk2);
 assert(mk2.irq_service_active && mk2.irq_requested && allocations==1);
 probe_policy(&cam);
 probe_policy(&fourk);
 assert(irq_service==1 && fourk.irq_service_active && allocations==2);
 irq_service=0;mk2=(struct device){.board=1,.name="mk2"};
 probe_policy(&mk2);
 assert(!mk2.irq_service_active && allocations==2);
 irq_service=1;alloc_result=0;
 probe_policy(&mk2);
 assert(!mk2.irq_service_active && frees==1 && requests==2);
 alloc_result=1;request_result=-1;
 probe_policy(&mk2);
 assert(!mk2.irq_requested && !mk2.irq_service_active && frees==2);
 return 0;
}
"""
with tempfile.TemporaryDirectory(prefix="sc0710-irq-test-") as d:
 p=Path(d);(p/"test.c").write_text(shim+policy+cases)
 subprocess.run([os.environ.get("CC","cc"),"-std=c11","-Wall","-Wextra","-Werror",str(p/"test.c"),"-o",str(p/"test")],check=True)
 subprocess.run([str(p/"test")],check=True)
print("PASS: mixed-card probe order, explicit polling, MSI allocation/request failures")
