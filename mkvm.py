#!/usr/bin/python
#============================================================================
# This library is free software; you can redistribute it and/or
# modify it under the terms of version 3.0 of the GNU General Public
# License as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#============================================================================
# Copyright (C) 2010 David Wahlstrom
# Copyright (C) 2010 Brett Lentz
#============================================================================
#
# By obtaining, using, and/or copying this software and/or its
# associated documentation, you agree that you have read, understood,
# and will comply with the following terms and conditions:
#
# Permission to use, copy, modify, and distribute this software and
# its associated documentation for any purpose and without fee is
# hereby granted, provided that the above copyright notice appears in
# all copies, and that both that copyright notice and this permission
# notice appear in supporting documentation, and that the name of
# Secret Labs AB or the author not be used in advertising or publicity
# pertaining to distribution of the software without specific, written
# prior permission.
#
# --------------------------------------------------------------------

import ConfigParser
import getpass
import logging
import os
import optparse
import shutil
import subprocess
import sys
import time
from time import strftime
import traceback
import xmlrpclib
import XenAPI

# Config file format:
#
# [vmname]
# fqdn = foo.env.loc.example.com
# type = resin|tomcat|mysql|other (pulls from template file. /etc/mkvm/templates by default)
# mgmt_classes = foo::bar baz wox
# profile = cobbler_profile
# vcpus = 1
# vram = 8
# nics = eth0 eth1 ethN
# vm_template = CentOS 5.3 x64 (this name resides in xenserver)

global_log_level = logging.WARN
default_log_format = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
default_log_handler = logging.StreamHandler(sys.stderr)
default_log_handler.setFormatter(default_log_format)

log = logging.getLogger("mkvm")
log.setLevel(global_log_level)
log.addHandler(default_log_handler)
log.debug("Starting logging")

default_config_file="/etc/mkvm/mkvm.conf"
default_template_file="/etc/mkvm/templates"

class VM:
    #instance variables.
    autostart = False
    cobbler_profile = ''
    fqdn = ''
    existing_vm = False
    hddsize = int(8 * 1024 * 1024 * 1024)
    ksmeta = {}
    ks_url = ''
    mgmt_classes = ''
    name = None
    nics = { 'eth0' : { 'dhcptag-eth0': '',
                        'gateway-eth0' : '',
                        'virtbridge-eth0' : '',
                        'dnsname-eth0' : fqdn,
                        'ipaddress-eth0' : '',
                        'bonding-eth0' : '',
                        'static-eth0' : False,
                        'macaddress-eth0' : '',
                      }
           }
    vcpus = 0
    vm_template = ''
    vmtype = 'default'
    vram = 0

    def __init__(self, name):
        """ name - the name used by XenCenter
            fqdn - the fully-qualified domain name of the VM used everywhere else """
        self.name = name

    def __repr__(self):
        return """
%s (%s):
    vcpus: %i
    vram: %i (bytes)
    nics: %s
    hddsize: %s (bytes)
    profile: %s
    mgmt_classes: %s
    storage: %s
    vm_template: %s
""" % (self.name, self.fqdn, int(self.vcpus), int(self.vram), self.nics, self.hddsize, self.cobbler_profile, self.mgmt_classes, self.storage, self.vm_template)

    def set_ks_url(self, cobbler_server):
        self.ks_url = 'http://' + cobbler_server + '/cblr/svc/op/ks/system/' + self.fqdn


class XenVM(VM):
    _gig = int(1024 * 1024 * 1024)
    aggr = None
    cobbler_server = None
    default_disk_size = int(8 * _gig)
    storage = None
    templateconfig = None
    userconfig = None
    sr_aggr = None

    def __init__(self, name, xencache):
        VM.__init__(self, name)
        self.xencache = xencache

    def _set_vmtype(self, vmtype):
        log.debug("in _set_vmtype()")
        self.cobbler_profile = self.templateconfig.get_item('profile', vmtype)
        self.mgmt_classes = self.templateconfig.get_item('mgmt_classes', vmtype)
        self.hddsize = int(self.templateconfig.get_item('hddsize', vmtype)) * self._gig
        self.vm_template = self.templateconfig.get_item('vm_template', vmtype)
        self.vcpus = self.templateconfig.get_item('vcpus', vmtype)
        self.vram = float(self.templateconfig.get_item('vram', vmtype)) * self._gig
        self.storage = self.templateconfig.get_item('storage', vmtype)
        self.usernics = self.templateconfig.get_item('nics', vmtype)

    def _set_user_config(self):
        log.debug("in _set_user_config()")

        # configure vm using default settings.
        vmtype = self.userconfig.get_item("type", self.name)
        if vmtype:
            self.vmtype = vmtype
            self._set_vmtype(self.vmtype)

        # cobbler management classes
        mc = self.userconfig.get_item("mgmt_classes", self.name)
        if mc:
            self.mgmt_classes = self.mgmt_classes + " " + mc
        
        # cobbler profile
        cp = self.userconfig.get_item("profile", self.name)
        if cp:
            self.cobbler_profile = cp

        # apply user-defined CPU settings...
        vcpu = self.userconfig.get_item("vcpus", self.name)
        if vcpu:
            self.vcpus = int(vcpu)

        # apply user-defined memory settings...
        vram = self.userconfig.get_item("vram", self.name)
        if vram:
            self.vram = int(vram) * int(self._gig)

        # apply user-defined template...
        vm_template = self.userconfig.get_item("vm_template", self.name)
        if vm_template:
            self.vm_template = vm_template

        # apply user-defined disk size settings...
        hddsize = self.userconfig.get_item("hddsize", self.name)
        if hddsize:
            self.hddsize = int(hddsize) * int(self._gig)

        log.info('App type is %s' % self.vmtype)
        if self.vmtype == 'resin':
            self.mgmt_classes = '%s resin::app::%s' % (self.mgmt_classes, self.hostname.split('-')[0])

        # dnsname always needs to be set.
        self.nics['eth0'] = { 'dnsname-eth0' : self.fqdn }
        usernics = self.userconfig.get_item(self.name, "nics")
        if usernics:
            for el in range(0, len(usernics)):
            # use range instead of the usual 'for item in list' idiom
            # so that python doesn't split strings into chars.
                iface = usernics[el]
                self.nics[iface] = {'dhcptag-'+iface : '',
                    'gateway-'+iface : '',
                    'virtbridge-'+iface : '',
                    'dnsname-'+iface : self.fqdn,
                    'ipaddress-'+iface : '',
                    'bonding-'+iface : '',
                    'static-'+iface : False,
                    'macaddress-'+iface : '',
                }

    def configure(self, userconfig, templateconfig, cobbler_server):
        log.debug("in configure()")
        self.userconfig = userconfig
        self.templateconfig = templateconfig
        self.cobbler_server = cobbler_server
        self.fqdn = self.userconfig.get_item('fqdn', self.name)
        self.hostname = self.fqdn.split('.')[0]
        self.env = self.fqdn.split('.')[1]
        self.loc = self.fqdn.split('.')[2]
        self.domain = '.'.join(self.fqdn.split('.')[1:])
        self.ksmeta = { 'dhclient' : True,
                        'xen' : True,
                        'domain' : self.domain
                      }
        self._set_vmtype('default')
        self._set_user_config()

    def create(self):
        """ this section will create the disk image for the VM, set its properties and prepare it to boot """

        log.debug("in create_vm()")
        log.info('Creating VM %s' % self.name)

        if not self.sr_aggr:
            log.warn('Unable to find shared storage!')

        # Find which aggregate to put the disk on
        self.aggr = self._find_best_aggr()

        self.vm_uuid = xenapi.VM.clone(xenapi.VM.get_by_name_label(self.vm_template)[0], self.name)
        xenapi.VM.set_is_a_template(self.vm_uuid, False)
        self.vm_uuid = self.vm_uuid

        log.info('new vm uuid is %s' % self.vm_uuid)

        xenapi.VM.set_VCPUs_max(self.vm_uuid, str(int(self.vcpus)))
        xenapi.VM.set_VCPUs_at_startup(self.vm_uuid, str(int(self.vcpus)))
        xenapi.VM.set_memory_dynamic_max(self.vm_uuid, str(int(self.vram)))
        xenapi.VM.set_memory_static_max(self.vm_uuid, str(int(self.vram)))
        xenapi.VM.set_PV_args(self.vm_uuid, "text ks=" + self.ks_url)
        try:
            xenapi.VM.remove_from_other_config(self.vm_uuid, 'HideFromXenCenter')
        except:
            pass
        xenapi.VM.add_to_other_config(self.vm_uuid, 'HideFromXenCenter', 'false')
        try:
            xenapi.VM.remove_from_other_config(self.vm_uuid, 'install-repository')
        except:
            pass
        if options.cblr_username:
            xenapi.VM.set_name_description(self.vm_uuid, "Created by " + str(options.cblr_username) + " using mkvm.py. " + strftime("%Y-%m-%d %H:%M:%S"))
        else:
            xenapi.VM.set_name_description(self.vm_uuid, "Created by " + getpass.getuser() + " using mkvm.py. " +strftime("%Y-%m-%d %H:%M:%S"))

        network_uuid = ''
        network_records = xenapi.network.get_all_records()
        # try to find the default network
        for k in network_records:
            if "other_config" in network_records[k] and 'automatic' in network_records[k]['other_config'] and network_records[k]['other_config']['automatic'] == 'true':
                network_uuid = k

        # if the default network can't be found, pick one at 'random'
        if not network_uuid:
            for k in network_records:
                network_uuid = k

        # if none of the above work, we will have not network connectvitivy
        if not network_uuid:
            network_uuid = True

        log.debug('network uuid is %s' % network_uuid)

        # create the VIF (network card)
        vif = { 'device': '0',
                'network': network_uuid,
                'VM': self.vm_uuid,
                'MAC': "",
                'MTU': '1500',
                'qos_algorithm_type': "",
                'qos_algorithm_params': {},
                'other_config': {},
              }
        vif_uuid = xenapi.VIF.create(vif)
        self.mac_addr = xenapi.VIF.get_record(vif_uuid)['MAC']

        #resize the disk if the template created one for the vm
        if xenapi.VM.get_VBDs(self.vm_uuid):
            log.info("Resizing VM disk to %sGB" % int(self.hddsize / 1024 / 1024 / 1024))
            vdb_uuid = xenapi.VM.get_VBDs(self.vm_uuid)
            vdi_uuid = xenapi.VDB.get_VDI(vdb_uuid)
            xenapi.VDI.resize(vdi_uuid, self.hddsize)
        else:
            # otherwise create a disk of the requested size
            log.info("Building a(n) %sGB disk" % int(self.hddsize / 1024 / 1024 / 1024))
            vdi = { 'read_only' : False ,
                    'sharable' : True ,
                    'SR' : str(xenapi.SR.get_by_name_label(self.aggr)[0]) ,
                    'name_label' : '/dev/xvda' ,
                    'name_description' : '/dev/xvda on ' + self.name ,
                    'virtual_size' : str(int(self.hddsize)) ,
                    'type' : 'system',
                    'other_config': { 'location': '/dev/xvda' } ,
                  }

            log.debug("VDI configuration: %s" % vdi)
            try:
                vdi_uuid = xenapi.VDI.create(vdi)
            except:
                print "Unable to create disk"
            log.debug("VDI uuid is %s" % vdi_uuid)
            
            # create a VBD to plug the VDI into the VM
            vbd = { 'VDI' : vdi_uuid,
                    'VM' : self.vm_uuid,
                    'mode' : 'RW',
                    'type' : 'Disk',
                    'userdevice' : 'xvda',
                    'bootable' : True,
                    'other_config': { 'owner' : '' },
                    'empty': False,
                    'qos_algorithm_type': '',
                    'qos_algorithm_params': {},
                  }
            log.debug("VBD configuration: %s" % vbd)
            vbd_uuid = xenapi.VBD.create(vbd)
            log.debug("VBD uuid is %s" % vbd_uuid)

        # start the vm, if desired.
        if self.autostart:
            xenapi.VM.power_state_reset(self.vm_uuid)
            self.start()
        else:
            xenapi.VM.power_state_reset(self.vm_uuid)

    def start(self):
        log.info('Booting %s' % self.name)

        try:
            xenapi.VM.start(vm_uuid, True, True)
        except:
            log.info('First attempt to start VM has FAILED. Will try 2 more times...')
            try:
                xenapi.VM.start(vm_uuid, True, True)
            except:
                log.info('Second attempt to start VM has FAILED.  Will try 1 more time...')
                try:
                    xenapi.VM.start(vm_uuid, True, True)
                except:
                    log.info('Unable to start VM')
                    pass

    def is_existing_vm(self):
        """ check if a VM of the same name already exists """
        self.existing_vm = []
        for vm in self.xencache._get_all_vm_records():
            if self.xencache._get_all_vm_records()[vm]['name_label'] == self.name:
                self.existing_vm.append(vm)
        
        return self.existing_vm

    def _find_best_aggr(self):
        """ probe available shared storage and determine which has the most free space """
        log.debug("in find_best_aggr()")
        log.debug('Storage UUID: %s' % self.sr_aggr())

        if self.xencache._get_shared_storage:
            sr_attrib = {}
            for sr in self.xencache._get_shared_storage():
                
                sruuid = xenapi.SR.get_by_name_label(sr)[0]
                srname = sr
                
                srphysusage = self.xencache._get_all_sr_records()[sruuid]['physical_utilisation']
                srphysusage = float(srphysusage)
                log.debug("%s's usage is %s" % (srname, srphysusage))
                
                srphyssize = self.xencache._get_all_sr_records()[sruuid]['physical_size']
                srphyssize = float(srphyssize)
                
                log.debug("%s's size is %s" % (srname, srphyssize))

                srusage = srphysusage / srphyssize
                log.debug("%s's usage ratio is %s" % (srname, srusage))
                sr_attrib[srusage] = srname

            best_aggr = sr_attrib[min(sr_attrib)]
            log.info('Best aggr is %s' % best_aggr)
            log.debug(sr_attrib)
            return best_aggr
        else:
            log.error("Unable to determine storage repository")
            return None

    def mac_addr():
        """ return the mac address of the VM """
        return self.mac_addr

    def fqdn():
        """ return FQDN of the VM """
        return self.fqdn


class ConfigFile:
    filename = None
    configparser = None

    def __init__(self, filename):
        self.filename = filename
        self.configparser = self._get_config(filename)

    def _get_config(self, myfile):
        log.debug("in get_config()")
        config = ConfigParser.ConfigParser()
        config.read(myfile)
        return config

    def get_item(self, cfgitem, section="default", hard_fail=False):
        log.debug("in _get_cfg_item()")

        def do_fail(err):
           if hard_fail:
              log.error(err)
              sys.exit(-1)
           else:
              log.info(err)

        item = None
        try:
           item = self.configparser.get(section, cfgitem)
        except ConfigParser.NoOptionError, e:
            do_fail(e)
        except ConfigParser.NoSectionError, e:
            do_fail(e)

        return item


class XenCache:
    """ stuff that needs to be 'discovered' once, then cached for future use """

    sr_aggr = None
    vm_templates = None
    all_vm_records = None
    vm_records = None

    def _query_xen_templates(self):
        """ probe the xenserver for available templates to use """
        log.debug('in _find_xen_templates')
        
        log.debug('scanning all templates in XenServer')
        template_uuids = {}
        for uuid in self.all_vm_records:
            for key in self.all_vm_records[uuid]:
                if 'is_a_template' in key and self.all_vm_records[uuid]['is_a_template']:
                    uuid_name_label = self.all_vm_records[uuid]['name_label']
                    templates = { uuid_name_label : uuid }
        templates = {}
        log.debug(templates)
        return templates

    def _query_shared_storage(self):
        """ probe for available storage backends """
        log.debug('in _find_shared_storage')

        log.debug('scanning all available backend storage devices')
        self.sr_aggr = {}
        for sr in self.all_sr_records:
            if 'shared' in self.all_sr_records[sr] and self.all_sr_records[sr]['shared']:
                if self.all_sr_records[sr]['type'] == 'lvmoiscsi' or self.all_sr_records[sr]['type'] == 'netapp':
                    self.sr_aggr[self.all_sr_records[sr]['name_label']] = self.all_sr_records[sr]['uuid']

        log.debug("Shared storage repositories found: %s" % self.sr_aggr)
        return self.sr_aggr

    def _query_sr_records(self):
        """ cache all the storage repository records, so we don't have to query XenServer for them multiple times """
        log.debug("in _get_sr_records()")
        
        sr_records = xenapi.SR.get_all_records()
        log.debug("Found SR records: %s" % sr_records)
        return sr_records
        
    def _query_vm_records(self):
        """ cache all the VM records, so we don't have to query XenServer for them multiple times """
        log.debug("in _get_vm_records()")
        
        vm_records = xenapi.VM.get_all_records()
        log.debug("Found VM records: %s" % vm_records)
        return vm_records

    def _get_shared_storage(self):
        return self.sr_aggr

    def _get_all_vm_records(self):
        return self.all_vm_records

    def _get_all_sr_records(self):
        return self.all_sr_records

    def _get_xen_templates(self):
        return self.vm_templates

    def __init__(self):
        self.all_sr_records = self._query_sr_records()
        self.all_vm_records = self._query_vm_records()
        self.vm_templates = self._query_xen_templates()
        self.sr_aggr = self._query_shared_storage()


class cobbler():
    """ everything related to cobbler """
    log.debug("in cobbler()")

    cobbler_server = ''
    cobbler = ''
    token = ''
    
    def get_cobbler_server(self, cblr):
        """ connect to a cobbler xmlrpc api """
        log.debug("in get_cobbler_server()")
        
        if cblr.startswith('http'):
            url = cblr
        else:
            url = "http://%s/cobbler_api" % cblr
            
        try:
            server = xmlrpclib.Server(url)
            return server
        except:
            traceback.print_exc()
            sys.exit(-1)

    def add_system_to_cobbler(self, xenvm):
        """ add the VM to cobbler """
        log.debug("in add_system_to_cobbler()")

        self.vm_id = self.cobbler.new_system(self.token)
        self.cobbler.modify_system(self.vm_id, 'name', xenvm.fqdn, self.token)
        self.cobbler.modify_system(self.vm_id, 'hostname', xenvm.fqdn, self.token)
        self.cobbler.modify_system(self.vm_id, 'profile', xenvm.cobbler_profile, self.token)
        self.cobbler.modify_system(self.vm_id, 'ksmeta', xenvm.ksmeta, self.token)
        self.cobbler.modify_system(self.vm_id, 'mgmt_classes', xenvm.mgmt_classes, self.token)
        self.cobbler.modify_system(self.vm_id, 'modify_interface', xenvm.nics['eth0'], self.token)
        self.cobbler.modify_system(self.vm_id, 'comment', 'Created by ' + options.cblr_username + ' using mkvm.py. ' + strftime("%Y-%m-%d %H:%M:%S"), self.token)
        self.cobbler.save_system(self.vm_id, self.token)

    def query_install_repo(self, vm_fqdn):
        """ query the install repo from cobbler """
        log.debug("in query_install_repo()")

        sys_rendered = self.cobbler.get_system_for_koan(vm_fqdn)
        for x in sys_rendered:
            if 'source_repo' in x:
                for y in sys_rendered[x]:
                    for z in y:
                        if z.endswith('.repo'):
                            pass
                        else:
                            install_repo = z.replace("@@http_server@@", self.cobbler_server)

        return install_repo

    def add_mac_to_cobbler(self, xenvm):
        """ add the MAC address to cobbler.  this will be moved into a class later """
        self.cobbler.modify_system(self.vm_id, 'modify_interface', xenvm.nics['eth0'], self.token)
        self.cobbler.save_system(self.vm_id, self.token)

    def purge(self, myvm):
        """ remove cobbler profile. this assumes that the cobbler profile matches the FQDN of the VM """
        pass
    
    def __init__(self, cobbler_server, options):
        # make initial cobbler connection
        self.cobbler = self.get_cobbler_server(cobbler_server)
        
        # try to login to the cobbler server
        try:
            self.token = self.cobbler.login(options.cblr_username, options.cblr_password)
        except xmlrpclib.Fault, e:
            log.error(e)
            sys.exit(-1)

def purge_vm(myvm, options, cobbler):
    """ this will shutdown and delete any existing VMs with the same xencenter name """
    log.debug("in purge_vm")
    log.info("destroying VM %s per your request." % myvm.name)
    
    if not options.skip_countdown:
        print ''
        print "DESTROYING %s" % myvm.name
        print "##### YOU HAVE 3 SECONDS TO INTERUPT THIS WITH CTRL+C #####"
        time.sleep(3)
        
    for existing_vm in myvm.is_existing_vm():
        vbd, vif, existing_VDIs = [], [], []
        existing_VBDs = xencache._get_all_vm_records()[existing_vm]['VBDs']
        existing_VIFs = xencache._get_all_vm_records()[existing_vm]['VIFs']

        log.debug('sending power off command to VM %s' % existing_vm) 
        try:
            log.info("powering off %s" % existing_vm)
            xenapi.VM.hard_shutdown(existing_vm)
        except:
            log.info("power off command failed. Assuming VM is already shutdown...")
            pass

        for uuid in existing_VBDs:
            existing_VDIs.append(xenapi.VBD.get_record(uuid)['VDI'])
            log.debug('sending destroy command for VBD %s' % uuid)
            try:
                xenapi.VBD.destroy(uuid)
            except:
                log.info("VBD destroy command failed. Assuming VBD is already destroyed...")
                pass

        for uuid in existing_VIFs:
            log.debug('sending destroy command for VIF %s' % uuid)
            try:
                xenapi.VIF.destroy(uuid)
            except:
                log.info("VIF destroy command failed. Assuming VIF is already destroyed...")
                pass

        for uuid in existing_VDIs:
            log.debug('sending destroy command for VDI %s' % uuid)
            try:
                xenapi.VDI.destroy(uuid)
            except:
                log.info("VDI destroy command failed.  Assuming VDI is already destroyed...")
                pass

        log.debug('sending destroy command for VM %s' % existing_vm)
        try:
            xenapi.VM.destroy(existing_vm)
        except:
            log.info("VM destroy command failed.  Assuming VM is already destroyed...")
            pass

        if options.add_to_cobbler:
            log.info("removing cobbler profile for %s" % myvm.fqdn)
            #cobbler.purge(myvm)
            log.info("VM (%s) was destroyed and its system profile (%s) was removed from cobbler" % (myvm.name, myvm.fqdn))
        else:
            log.info("VM (%s) was destroyed" % myvm.name)


def get_options():
    """ command-line options """
    log.debug("in get_options()")

    usage = "usage: %prog -f <FILE> [options]"
    OptionParser = optparse.OptionParser
    parser = OptionParser(usage)

    required = optparse.OptionGroup(parser, "Required")
    optional = optparse.OptionGroup(parser, "Optional")

    required.add_option("-f", "--file", dest="vmfile", action="store", type="string",
                     help="Use FILE as input file.", metavar="FILE")
    optional.add_option("-v", "--verbose", action="store_true", dest="debug", default=False,
                     help="Enable verbose output.")
    optional.add_option("-a", "--autostart", action="store_true", dest="autostart",
                     help="Create a VM and send it the boot signal (-s to keep it off).")
    optional.add_option("-c", "--skip-cobbler", action="store_false", dest="add_to_cobbler", default=True,
                     help="Don't add a VM to cobbler. By default mkvm adds a new system to Cobbler.")
    optional.add_option("-u", "--username", action="store", type="string", dest="cblr_username",
                     help="Username on cobbler server. Will prompt if not passed on command line.")
    optional.add_option("-p", "--password", action="store", type="string", dest="cblr_password",
                     help="Password on cobbler server. Will prompt if not passed on command line.")
    optional.add_option("-r", "--replace", action="store_true", dest="replace",
                     help="Replace existing VMs with same hostname.  Assumes -i.  This will shutdown and delete ALL VMs with matching hostname.  Use with care.")
    optional.add_option("-i", "--ignore-existing-vm", action="store_true", dest="ignore",
                     help="Ignores possible conflicts, such as existing cobbler system profiles or existing duplicate VMs.")
    optional.add_option("-d", "--destoy", action="store_true", dest="destroy",
                     help="Destoy VMs with matching names.  This is totally destructive, so use with care.")
    optional.add_option("-s", "--skip-countdown", action="store_true", dest="skip_countdown",
                     help="Skips the countdown before destroying VM.  Use this with -d to quickly destroy VMs.")
    optional.add_option("-t", "--template", action="store", dest="template_file", type="string",
                     help="Load templates from the given file.  Default: /etc/mkvm/templates")

    parser.add_option_group(required)
    parser.add_option_group(optional)
    options, args = parser.parse_args()

    if options.debug:
        log.setLevel(logging.DEBUG)

    if options.replace:
        options.ignore = True
    if not options.vmfile:
        parser.print_help()
        sys.exit(-1)

    if options.add_to_cobbler:
        if not options.cblr_username:
            options.cblr_username = raw_input('Cobbler Username:')
        if not options.cblr_password:
            options.cblr_password = getpass.getpass('Cobbler Password:')

    if not options.vmfile:
        options.vmfile = default_config_file

    if not options.template_file:
        options.template_file = default_template_file

    return options


if __name__ == "__main__":
    log.debug("in __main__()")
    
    options = get_options()
    default_configs = ConfigFile(default_config_file)
    
    xenserver = default_configs.get_item('xenserver')
    if not xenserver:
        xenserver = raw_input('XenServer (Hostname or IP): ')
    xenserver_username = default_configs.get_item('xenserver_username')
    if not xenserver_username:
        xenserver_username = raw_input('XenServer Username: ')
    xenserver_password = default_configs.get_item('xenserver_password')
    if not xenserver_password:
        xenserver_password = getpass.getpass('XenServer Password: ')
    if not xenserver.startswith('http'):
        xenserver = 'https://' + xenserver + '/'
    xensession = XenAPI.Session(xenserver)
    xensession.login_with_password(xenserver_username, xenserver_password)
    xenapi = xensession.xenapi

    cobbler_server = default_configs.get_item('cobbler_server')
    if options.add_to_cobbler:
        # connect to cobbler's xmlrpc API
        cobbler = cobbler(cobbler_server, options)

    cfg = ConfigFile(options.vmfile) # user vm config.
    tmpl = ConfigFile(options.template_file) # default config templates.

    xencache = XenCache()

    vmlist = []
    for vmname in cfg.configparser.sections():
        log.debug("setting up %s" % vmname)
        myvm = XenVM(vmname, xencache)

        # cache some xen information so we don't have to query xenserver multiple times for the same data
        myvm.sr_aggr = xencache._get_shared_storage
        myvm.vm_template = xencache._get_xen_templates
        
        # apply configurations from either the template or user supplied values
        myvm.configure(cfg, tmpl, cobbler_server)

        log.debug("Created new XenVM object: %s" % str(myvm))

        # if invoked to delete VMs, run through the input file and delete all matches
        if options.destroy:
            purge_vm(myvm, options, cobbler)
            continue
        
        # warn the user before creating an identical VM
        if myvm.is_existing_vm() and not options.ignore:
            log.error('%s already exists. Aborting creation of %s. To ignore this and create it anyway, use -i. To REPLACE (destroy the existing and build a new one) this VM, use -r.' % \
                (myvm.name, myvm.name))
            time.sleep(2)
            continue
        else:
            if options.replace:
                purge_vm(myvm, options, cobbler)

            myvm.set_ks_url(cobbler_server)
            if options.add_to_cobbler:
                log.debug("Adding %s to cobbler" % myvm.name)
                # add the new system to cobbler
                cobbler.add_system_to_cobbler(myvm)
            if options.autostart:
                myvm.autostart = True
            
            # actually create the VM
            myvm.create()
            
            # add the install repository location for kickstart
            if options.add_to_cobbler:
                log.info("adding install repo to VM %s" % myvm.vm_uuid)
                xenapi.VM.add_to_other_config(myvm.vm_uuid, 'install-repository', cobbler.query_install_repo(myvm.fqdn))
                myvm.nics['eth0']['macaddress-eth0'] = myvm.mac_addr
                cobbler.add_mac_to_cobbler(myvm)

            if options.autostart:
                log.info("sending start command to VM %s" % myvm.vm_uuid)
                xenapi.VM.start(myvm.vm_uuid)
