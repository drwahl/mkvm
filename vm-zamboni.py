#!/usr/bin/python

import time
import xmlrpclib
import logging
import sys

global_log_level = logging.WARN
default_log_file = '/var/log/mkvm/vm-zamboni.log'
default_activity_log_file = '/var/log/mkvm/activity.log'
default_log_format = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

logging.basicConfig(filename=default_log_file,
                    level=logging.DEBUG,
                    format='%(asctime)s %(name)s - %(levelname)s - %(message)s',
                    datefmt='%y.%m.%d %H:%M:%S'
                   )
console = logging.StreamHandler(sys.stderr)
console.setLevel(logging.WARN)
formatter = logging.Formatter('%(name)s: %(levelname)-8s %(message)s')
console.setFormatter(formatter)
logging.getLogger("v-zambonim").addHandler(console)

log = logging.getLogger("vm-zamboni")
log.debug("Starting log")

while True:
    xenapi = xmlrpclib.Server('https://xenserver01.corp.sea1.cmates.com')
    xensession = xenapi.session.login_with_password('root', 'xencenter')['Value']

    date = int(time.time())
    all_vm_records = xenapi.VM.get_all_records(xensession)['Value']

    for vm_uuid in all_vm_records:
        if not all_vm_records[vm_uuid]['is_control_domain']:
            if 'expiry' in all_vm_records[vm_uuid]['other_config']:
                if int(all_vm_records[vm_uuid]['other_config']['expiry']) < date:
                    activity_log = open(default_activity_log_file, 'a')
                    activity_log.write('%s: %s purged VM %s\n' % (time.strftime("%Y-%m-%d %H:%M:%S"), "vm-zamboni", all_vm_records[vm_uuid]['name_label']))
                    activity_log.close

		    vmcache = xenapi.VM.get_record(xensession, vm_uuid)['Value']
                    vbd, vif, VDIs = [], [], []
                    VBDs = vmcache['VBDs']
                    VIFs = vmcache['VIFs']
                    log.info('sending power off command to VM %s' % vm_uuid) 
                    try:
                        log.debug("powering off %s" % vm_uuid)
                        xenapi.VM.hard_shutdown(xensession, vm_uuid)
                    except:
                        log.debug("power off command failed. Assuming VM is already shutdown...")
                    pass
                    
                    for uuid in VBDs:
                        VDIs.append(xenapi.VBD.get_record(xensession, uuid)['Value']['VDI'])
                        log.info('sending destroy command for VBD %s' % uuid)
                        try:
                            log.debug("destroying VBD %s" % uuid)
                            xenapi.VBD.destroy(xensession, uuid)
                        except:
                            log.debug("VBD destroy command failed. Assuming VBD is already destroyed...")
                            pass
                    
                    for uuid in VIFs:
                        log.info('sending destroy command for VIF %s' % uuid)
                        try:
                            log.debug("destroying VIF %s" % uuid)
                            xenapi.VIF.destroy(xensession, uuid)
                        except:
                            log.debug("VIF destroy command failed. Assuming VIF is already destroyed...")
                            pass
                    
                    for uuid in VDIs:
                        log.info('sending destroy command for VDI %s' % uuid)
                        try:
                            log.debug("destroying VDI %s" % uuid)
                            xenapi.VDI.destroy(xensession, uuid)
                        except:
                            log.debug("VDI destroy command failed.  Assuming VDI is already destroyed...")
                            pass
                    
                    log.info('sending destroy command for VM %s' % vm_uuid)
                    try:
                        log.debug("destroying VM %s" % vm_uuid)
                        xenapi.VM.destroy(xensession, vm_uuid)
                    except:
                        log.debug("VM destroy command failed.  Assuming VM is already destroyed...")
                        pass
                    
                    log.info("VM (%s) was destroyed" % vmcache['name_label'])

    xenapi.session.logout(xensession)

    time.sleep(300)
