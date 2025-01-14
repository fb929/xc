#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# base import {{
import os
from os.path import expanduser
import sys
import argparse
import yaml
import re
import logging
import inspect
# }}

# local import {{
import traceback
import subprocess
import json
# }}

# base {{
# default vars
scriptName          = os.path.basename(sys.argv[0]).split('.')[0]
homeDir             = expanduser("~")
defaultConfigFiles  = [
    '/etc/' + scriptName + '/config.yaml',
    homeDir + '/.' + scriptName + '.yaml',
]
cfg = {
    'logFile': '/var/log/' + scriptName + '/' + scriptName + '.log',
    'logFile': 'stdout',
    'logLevel': 'info',
    'regions': [],
    'iniFilePath': '~/xcdata.ini',
    'tagForMainGroup': 'Name', # tag 'Key' for 'mainGroup', all others tags will be added in 'parent' or 'tags' groups
    'tagForParentGroup': 'role', # tag 'Key' for 'parentGroup'
    'workgroup': 'devops', # name for default 'workgroup'
    'awsHostField': 'PublicDnsName', # field for 'host' in aws data, variants: PublicDnsName, PublicIpAddress, PrivateDnsName, PrivateIpAddress
}

# parse args
parser = argparse.ArgumentParser( description = '''
default config files: %s

''' % ', '.join(defaultConfigFiles),
formatter_class=argparse.RawTextHelpFormatter
)
parser.add_argument(
    '-c',
    '--config',
    help = 'path to config file',
)
args = parser.parse_args()
argConfigFile = args.config

# get settings
if argConfigFile:
    if os.path.isfile(argConfigFile):
        try:
            with open(argConfigFile, 'r') as ymlfile:
                cfg.update(yaml.load(ymlfile,Loader=yaml.Loader))
        except Exception as e:
            logging.error('failed load config file: "%s", error: "%s"', argConfigFile, e)
            exit(1)
else:
    for configFile in defaultConfigFiles:
        if os.path.isfile(configFile):
            try:
                with open(configFile, 'r') as ymlfile:
                    try:
                        cfg.update(yaml.load(ymlfile,Loader=yaml.Loader))
                    except Exception as e:
                        logging.warning('skipping load load config file: "%s", error "%s"', configFile, e)
                        continue
            except:
                continue

# fix logDir
cfg['logDir'] = os.path.dirname(cfg['logFile'])
if cfg['logDir'] == '':
    cfg['logDir'] = '.'
# }}

# defs
def runCmd(commands,communicate=True,stdoutJson=True):
    """ запуск shell команд, вернёт хеш:
    {
        "stdout": stdout,
        "stderr": stderr,
        "exitCode": exitCode,
    }
    """

    defName = inspect.stack()[0][3]
    logging.debug("%s: '%s'" % (defName,commands))
    if communicate:
        process = subprocess.Popen('/bin/bash', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        out, err = process.communicate(commands.encode())
        returnCode = process.returncode
        try:
            outFormatted = out.rstrip().decode("utf-8")
        except:
            outFormatted = out.decode("utf-8")
        if stdoutJson:
            try:
                stdout = json.loads(outFormatted)
            except Exception:
                logging.error("%s: failed runCmd, cmd='%s', error='%s'" % (defName,commands,traceback.format_exc()))
                return None
        else:
            stdout = outFormatted
        return {
            "stdout": stdout,
            "stderr": err,
            "exitCode": returnCode,
        }
    else:
        subprocess.call(commands, shell=True)
        return None

def getInstancesInfo(region):
    defName = inspect.stack()[0][3]

    instancesInfo = list()
    describeInstances = runCmd("aws ec2 --region %s describe-instances" % region)['stdout']
    reservations = describeInstances['Reservations']
    for reservation in reservations:
        instances = reservation['Instances']
        for instance in instances:
            if instance['State']['Name'] != 'running':
                continue
            info = {
                'dc': instance['Placement']['AvailabilityZone'],
                'tags': instance['Tags'],
                'host': instance[cfg['awsHostField']],
            }
            if info not in instancesInfo:
                instancesInfo.append(info)
    return instancesInfo

if __name__ == "__main__":
    # basic config {{
    for dirPath in [
        cfg['logDir'],
    ]:
        try:
            os.makedirs(dirPath)
        except OSError:
            if not os.path.isdir(dirPath):
                raise

    # loglevel choice
    if re.match(r"^(warn|warning)$", cfg['logLevel'], re.IGNORECASE):
        logLevel = logging.WARNING
    elif re.match(r"^debug$", cfg['logLevel'], re.IGNORECASE):
        logLevel = logging.DEBUG
    else:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)
        logLevel = logging.INFO

    if cfg['logFile'] == 'stdout':
        logging.basicConfig(
            level       = logLevel,
            format      = '%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s',
            datefmt     = '%Y-%m-%dT%H:%M:%S',
        )
    else:
        logging.basicConfig(
            filename    = cfg['logFile'],
            level       = logLevel,
            format      = '%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s',
            datefmt     = '%Y-%m-%dT%H:%M:%S',
        )
    # }}

    defName = "main"
    logging.debug("%s: cfg='%s'" % (defName,json.dumps(cfg,indent=4)))

    if cfg['regions']:
        regions = cfg['regions']
    else:
        regions = runCmd("aws ec2 describe-regions --query 'Regions[].RegionName'")['stdout']
    instancesInfoArray = list()
    for region in regions:
        instancesInfoArray = instancesInfoArray + getInstancesInfo(region)

    logging.debug("%s: instancesInfoArray='%s'" % (defName,json.dumps(instancesInfoArray,indent=4)))

    datacenters = list()
    groups = list()
    hosts = list()
    for instanceInfo in instancesInfoArray:
        # adding data-center in dc list
        if instanceInfo['dc'] not in datacenters:
            datacenters.append(instanceInfo['dc'])
        # making "workgroups"
        # "workgroups" it's required param, without this param, tags= not worked
        workgroups = [ cfg['workgroup'] ] # get default from cfg
        # sorting tags by types {{
        mainGroupName = None
        parentGroupName = None
        tagsGroupNames = None
        for tag in instanceInfo['tags']:
            groupName = 'tag' + '_' + tag['Key'].replace('-','_') + '_' + tag['Value'].replace('-','_')
            groupName = re.sub(r'\s+', '_', groupName)
            if tag['Key'] == cfg['tagForMainGroup']:
                # from this tag making "main group"
                mainGroupName = groupName
            elif tag['Key'] == cfg['tagForParentGroup']:
                # from this tag making "parant group"
                parentGroupName = groupName
                # and added in "all group" list
                if parentGroupName not in groups:
                    groups.append(parentGroupName)
            else:
                # if not matched anything, then adding in "xcdata tags"
                if tagsGroupNames:
                    tagsGroupNames = tagsGroupNames + ',' + groupName
                else:
                    tagsGroupNames = groupName
        # }}
        # making string for section [groups] {{
        if mainGroupName:
            groupLine = mainGroupName + ' wg=' + cfg['workgroup']
        else:
            # mainGroupName required
            logging.warning("%s: mainGroupName not found for host, instanceInfo='%s', skipping" % (defName,json.dumps(instanceInfo)))
            continue
        if parentGroupName:
            groupLine = groupLine + ' parent=' + parentGroupName
        if tagsGroupNames:
            groupLine = groupLine + ' tags=' + tagsGroupNames
        if groupLine not in groups:
            groups.append(groupLine)
        # }}

        # making string for section [hosts]
        host = instanceInfo['host'] + ' group=' + mainGroupName + ' dc=' + instanceInfo['dc']
        if host not in hosts:
            hosts.append(host)

    logging.debug("%s: datacenters='%s'" % (defName,datacenters))
    logging.debug("%s: workgroups='%s'" % (defName,workgroups))
    logging.debug("%s: groups='%s'" % (defName,groups))
    logging.debug("%s: hosts='%s'" % (defName,hosts))

    # making xcdata.ini {{
    config = '[datacenters]'
    for datacenter in datacenters:
        config = config + '\n' + str(datacenter)
    config = config + '\n\n' + '[workgroups]'
    for workgroup in workgroups:
        config = config + '\n' + str(workgroup)
    config = config + '\n\n' + '[groups]'
    for group in groups:
        config = config + '\n' + str(group)
    config = config + '\n\n' + '[hosts]'
    for host in hosts:
        config = config + '\n' + str(host)
    config = config + '\n'
    logging.debug("%s: config='%s'" % (defName,config))
    with open(os.path.expanduser(cfg['iniFilePath']), 'w') as f:
        f.write(config)
    # }}
