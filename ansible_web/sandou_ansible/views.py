# -*-coding:utf-8 -*-
from django.shortcuts import render
from sandou_ansible.models import PlayBookJob
from django.http import HttpResponse
import json

# Create your views here.

baseDir = "/Users/smart/Documents/ansible_auto/"

def parse_host_info(info):
    all_list = []
    for (ip,server) in zip(info['ips'],info['servers']):
          all_list.append({ip:server})
    return all_list


def index(request):
    return render(request, 'index.html')

def post_result(request):
    servers = []
    results = ""
    ips = request.POST.getlist('ips[]')
    host_password = request.POST.getlist('ssh_pass')
    extra_list = request.POST.getlist('extra_vars')
    if extra_list != [""]:
        extra_vars = extra_list[0].split(',')
        extra_dict = {'remote_host':extra_vars[0],'remote_activity':extra_vars[1]}
    else:
        extra_dict = None
    for i in range(len(ips)):
        servers.append(request.POST.getlist('servers['+ str(i) +'][]'))
    init_info = {'ips':ips,'servers':servers}
    infos = parse_host_info(init_info)
    print("----------------start------------------")
    for info in infos:
        for item in info:
            value = info[item]
            install_path = [baseDir + i + "/" + i + ".yml" for i in value]
            result = PlayBookJob(playbooks=install_path, host_list=[item], passwords=host_password[0], ssh_user='root',extra_vars=extra_dict,
                                 forks=10).run()
            results += result+"\n"
            print(result)
    print("----------------end------------------")
    json_result = json.dumps({'msg':results,'status':200})
    return HttpResponse(json_result)