#!/usr/bin/python

import time
import xmlrpclib
#import mkvm

while True:
    xenapi = xmlrpclib.Server('https://xenserver01.corp.sea1.cmates.com')
    xensession = xenapi.session.login_with_password('root', 'xencenter')['Value']

    date = int(time.time())
    all_vm_records = xenapi.VM.get_all_records(xensession)['Value']

    for vm_uuid in all_vm_records:
        if not all_vm_records[vm_uuid]['is_control_domain']:
            if 'expiry' in all_vm_records[vm_uuid]['other_config']:
                if int(all_vm_records[vm_uuid]['other_config']['expiry']) < date:
                    print "%s deleted" % all_vm_records[vm_uuid]['name_label']
                    #for existing_vm in myvm.is_existing_vm():
                    #    vbd, vif, existing_VDIs = [], [], []
                    #    existing_VBDs = xencache._get_all_vm_records()[existing_vm]['VBDs']
                    #    existing_VIFs = xencache._get_all_vm_records()[existing_vm]['VIFs']
                    #log.info('sending power off command to VM %s' % existing_vm) 
                    #try:
                    #    log.debug("powering off %s" % existing_vm)
                    #    xenapi.VM.hard_shutdown(xensession, existing_vm)
                    #except:
                    #    log.debug("power off command failed. Assuming VM is already shutdown...")
                    #pass
                    #
                    #for uuid in existing_VBDs:
                    #    existing_VDIs.append(xenapi.VBD.get_record(xensession, uuid)['Value']['VDI'])
                    #    log.info('sending destroy command for VBD %s' % uuid)
                    #    try:
                    #        log.debug("destroying VBD %s" % uuid)
                    #        xenapi.VBD.destroy(xensession, uuid)
                    #    except:
                    #        log.debug("VBD destroy command failed. Assuming VBD is already destroyed...")
                    #        pass
                    #
                    #for uuid in existing_VIFs:
                    #    log.info('sending destroy command for VIF %s' % uuid)
                    #    try:
                    #        log.debug("destroying VIF %s" % uuid)
                    #        xenapi.VIF.destroy(xensession, uuid)
                    #    except:
                    #        log.debug("VIF destroy command failed. Assuming VIF is already destroyed...")
                    #        pass
                    #
                    #for uuid in existing_VDIs:
                    #    log.info('sending destroy command for VDI %s' % uuid)
                    #    try:
                    #        log.debug("destroying VDI %s" % uuid)
                    #        xenapi.VDI.destroy(xensession, uuid)
                    #    except:
                    #        log.debug("VDI destroy command failed.  Assuming VDI is already destroyed...")
                    #        pass
                    #
                    #log.info('sending destroy command for VM %s' % existing_vm)
                    #try:
                    #    log.debug("destroying VM %s" % existing_vm)
                    #    xenapi.VM.destroy(xensession, existing_vm)
                    #except:
                    #    log.debug("VM destroy command failed.  Assuming VM is already destroyed...")
                    #    pass
                    #
                    #if options.add_to_cobbler:
                    #    log.info("removing cobbler profile for %s" % myvm.fqdn)
                    #    cobbler.purge(myvm)
                    #    log.info("VM (%s) was destroyed and its system profile (%s) was removed from cobbler" % (myvm.name, myvm.fqdn))
                    #    else:
                    #        log.info("VM (%s) was destroyed" % myvm.name)

                else:
                    print "%s saved" % all_vm_records[vm_uuid]['name_label']

    xenapi.session.logout(xensession)

    time.sleep(300)
