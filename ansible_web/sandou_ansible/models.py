from django.db import models

# Create your models here.

from ansible.executor.task_queue_manager import TaskQueueManager
from ansible.utils.ssh_functions import check_for_controlpersist
from ansible import constants as C
from collections import namedtuple
from ansible.parsing.dataloader import DataLoader
from ansible.vars import VariableManager
from ansible.inventory import Inventory
from ansible.executor.playbook_executor import PlaybookExecutor
from datetime import datetime
from ansible.plugins.callback import CallbackBase

class SandouPlaybookExecutor(PlaybookExecutor):
    def __init__(self, playbooks, inventory, variable_manager, loader, options, passwords, stdout_callback=None):
        self._playbooks = playbooks
        self._inventory = inventory
        self._variable_manager = variable_manager
        self._loader = loader
        self._options = options
        self.passwords = passwords
        self._unreachable_hosts = dict()

        if options.listhosts or options.listtasks or options.listtags or options.syntax:
            self._tqm = None
        else:
            self._tqm = TaskQueueManager(inventory=inventory, variable_manager=variable_manager, loader=loader,
                                         options=options, passwords=self.passwords, stdout_callback=stdout_callback)

        # Note: We run this here to cache whether the default ansible ssh
        # executable supports control persist.  Sometime in the future we may
        # need to enhance this to check that ansible_ssh_executable specified
        # in inventory is also cached.  We can't do this caching at the point
        # where it is used (in task_executor) because that is post-fork and
        # therefore would be discarded after every task.
        check_for_controlpersist(C.ANSIBLE_SSH_EXECUTABLE)


class PlayBookJob(object):
  def __init__(self,playbooks,host_list,ssh_user='root',passwords='',extra_vars=None,forks=5):
    self.playbooks = playbooks
    self.host_list = host_list
    self.ssh_user  = ssh_user
    self.passwords = dict(conn_pass=passwords)
    self.forks     = forks
    self.connection='smart'
    self.extra_vars= extra_vars

    ## 用来加载解析yaml文件或JSON内容,并且支持vault的解密
    self.loader    = DataLoader()

    # 管理变量的类，包括主机，组，扩展等变量，之前版本是在 inventory中的
    self.variable_manager = VariableManager()
    if extra_vars:
        self.variable_manager.extra_vars = extra_vars

    # 根据inventory加载对应变量
    self.inventory = Inventory(loader=self.loader,
                               variable_manager=self.variable_manager,
                               host_list=self.host_list)

    self.variable_manager.set_inventory(self.inventory)

    # 初始化需要的对象1
    self.Options = namedtuple('Options',
                             ['connection',
                             'remote_user',
                             'ask_sudo_pass',
                             'verbosity',
                             'module_path',
                             'forks',
                             'become',
                             'become_method',
                             'become_user',
                             'check',
                             'listhosts',
                             'listtasks',
                             'listtags',
                             'syntax',
                             'sudo_user',
                             'sudo'
                             ])

    # 初始化需要的对象2
    self.options = self.Options(connection=self.connection,
                                remote_user=self.ssh_user,
                                sudo_user=self.ssh_user,
                                forks=self.forks,
                                sudo='yes',
                                ask_sudo_pass=False,
                                verbosity=10,
                                module_path=None,
                                become=True,
                                become_method='sudo',
                                become_user='root',
                                check=None,
                                listhosts=None,
                                listtasks=None,
                                listtags=None,
                                syntax=None
                               )

    # 初始化console输出
    self.callback = sandouCallbackModule()
    # 直接开始

  def run(self):
    pb = SandouPlaybookExecutor(
        playbooks            = self.playbooks,
        inventory            = self.inventory,
        variable_manager     = self.variable_manager,
        loader               = self.loader,
        options              = self.options,
        passwords            = self.passwords,
        stdout_callback      = self.callback
    )
    pb.run()
    pb._tqm.send_callback('record_logs')
    result = pb._tqm._stdout_callback.record_logs()
    return result



class PlayLogger:
    def __init__(self):
        self.log = ''
        self.runtime = 0

    def append(self, log_line):
        """append to log"""
        self.log += log_line+"\n"

    def banner(self, msg):
        """Output Trailing Stars"""
        width = 78 - len(msg)
        if width < 3:
            width = 3
        filler = "*" * width
        return "\n%s %s " % (msg, filler)

class sandouCallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'stored'
    CALLBACK_NAME = 'database'

    def __init__(self):
        super(sandouCallbackModule, self).__init__()
        self.logger = PlayLogger()
        self.start_time = datetime.now()

    def v2_runner_on_failed(self, result, ignore_errors=False):
        delegated_vars = result._result.get('_ansible_delegated_vars', None)

        # Catch an exception
        # This may never be called because default handler deletes
        # the exception, since Ansible thinks it knows better
        if 'exception' in result._result:
            # Extract the error message and log it
            error = result._result['exception'].strip().split('\n')[-1]
            self.logger.append(error)

            # Remove the exception from the result so it's not shown every time
            del result._result['exception']

        # Else log the reason for the failure
        if result._task.loop and 'results' in result._result:
            self._process_items(result)  # item_on_failed, item_on_skipped, item_on_ok
        else:
            if delegated_vars:
                self.logger.append("fatal: [%s -> %s]: FAILED! => %s" % (
                result._host.get_name(), delegated_vars['ansible_host'], self._dump_results(result._result)))
            else:
                self.logger.append(
                    "fatal: [%s]: FAILED! => %s" % (result._host.get_name(), self._dump_results(result._result)))

    def v2_runner_on_ok(self, result):
        self._clean_results(result._result, result._task.action)
        delegated_vars = result._result.get('_ansible_delegated_vars', None)
        if result._task.action == 'include':
            return
        elif result._result.get('changed', False):
            if delegated_vars:
                msg = "changed: [%s -> %s]" % (result._host.get_name(), delegated_vars['ansible_host'])
            else:
                msg = "changed: [%s]" % result._host.get_name()
        else:
            if delegated_vars:
                msg = "ok: [%s -> %s]" % (result._host.get_name(), delegated_vars['ansible_host'])
            else:
                msg = "ok: [%s]" % result._host.get_name()

        if result._task.loop and 'results' in result._result:
            self._process_items(result)  # item_on_failed, item_on_skipped, item_on_ok
        else:
            self.logger.append(msg)

    def v2_runner_on_skipped(self, result):
        if result._task.loop and 'results' in result._result:
            self._process_items(result)  # item_on_failed, item_on_skipped, item_on_ok
        else:
            msg = "skipping: [%s]" % result._host.get_name()
            self.logger.append(msg)

    def v2_runner_on_unreachable(self, result):
        delegated_vars = result._result.get('_ansible_delegated_vars', None)
        if delegated_vars:
            self.logger.append("fatal: [%s -> %s]: UNREACHABLE! => %s" % (
            result._host.get_name(), delegated_vars['ansible_host'], self._dump_results(result._result)))
        else:
            self.logger.append(
                "fatal: [%s]: UNREACHABLE! => %s" % (result._host.get_name(), self._dump_results(result._result)))

    def v2_runner_on_no_hosts(self, task):
        self.logger.append("skipping: no hosts matched")

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.logger.append("TASK [%s]" % task.get_name().strip())

    def v2_playbook_on_play_start(self, play):
        name = play.get_name().strip()
        if not name:
            msg = "PLAY"
        else:
            msg = "PLAY [%s]" % name

        self.logger.append(msg)

    def v2_playbook_item_on_ok(self, result):
        delegated_vars = result._result.get('_ansible_delegated_vars', None)
        if result._task.action == 'include':
            return
        elif result._result.get('changed', False):
            if delegated_vars:
                msg = "changed: [%s -> %s]" % (result._host.get_name(), delegated_vars['ansible_host'])
            else:
                msg = "changed: [%s]" % result._host.get_name()
        else:
            if delegated_vars:
                msg = "ok: [%s -> %s]" % (result._host.get_name(), delegated_vars['ansible_host'])
            else:
                msg = "ok: [%s]" % result._host.get_name()

        msg += " => (item=%s)" % (result._result['item'])

        self.logger.append(msg)

    def v2_playbook_item_on_failed(self, result):
        delegated_vars = result._result.get('_ansible_delegated_vars', None)
        if 'exception' in result._result:
            # Extract the error message and log it
            error = result._result['exception'].strip().split('\n')[-1]
            self.logger.append(error)

            # Remove the exception from the result so it's not shown every time
            del result._result['exception']

        if delegated_vars:
            self.logger.append("failed: [%s -> %s] => (item=%s) => %s" % (
            result._host.get_name(), delegated_vars['ansible_host'], result._result['item'],
            self._dump_results(result._result)))
        else:
            self.logger.append("failed: [%s] => (item=%s) => %s" % (
            result._host.get_name(), result._result['item'], self._dump_results(result._result)))

    def v2_playbook_item_on_skipped(self, result):
        msg = "skipping: [%s] => (item=%s) " % (result._host.get_name(), result._result['item'])
        self.logger.append(msg)

    def v2_playbook_on_stats(self, stats):
        run_time = datetime.now() - self.start_time
        self.logger.runtime = run_time.seconds  # returns an int, unlike run_time.total_seconds()

        hosts = sorted(stats.processed.keys())
        for h in hosts:
            t = stats.summarize(h)

            msg = "Result : %s %s %s %s %s %s秒" % (
                "ok: %s" % (t['ok']),
                "changed: %s" % (t['changed']),
                "unreachable: %s" % (t['unreachable']),
                "skipped: %s" % (t['skipped']),
                "failed: %s" % (t['failures']),
                "run_time: %s" % (self.logger.runtime),
            )

            self.logger.append(msg)

    def record_logs(self):
        return self.logger.log