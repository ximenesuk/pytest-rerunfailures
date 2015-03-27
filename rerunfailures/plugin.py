import sys, time
import py, pytest

from _pytest.runner import call_and_report
from _pytest.runner import runtestprotocol

# command line options
def pytest_addoption(parser):
    group = parser.getgroup("rerunfailures", "re-run failing tests to eliminate flakey failures")
    group._addoption('--reruns',
        action="store",
        dest="reruns",
        type="int",
        default=0,
        help="number of times to re-run failed tests. defaults to 0.")


def pytest_configure(config):
    #Add flaky marker
    config.addinivalue_line("markers", "flaky(reruns=1): mark test to re-run up to 'reruns' times")

# making sure the options make sense
# should run before / at the begining of pytest_cmdline_main
def check_options(config):
    val = config.getvalue
    if not val("collectonly"):
        if config.option.reruns != 0:
            if config.option.usepdb:   # a core option
                raise pytest.UsageError("--reruns incompatible with --pdb")


def pytest_runtest_protocol(item, nextitem):
    """
    Note: when teardown fails, two reports are generated for the case, one for the test
    case and the other for the teardown error.

    Note: in some versions of py.test, when setup fails on a test that has been marked with xfail, 
    it gets an XPASS rather than an XFAIL 
    (https://bitbucket.org/hpk42/pytest/issue/160/an-exception-thrown-in)
    fix should be released in version 2.2.5
    """

    if not hasattr(item, 'get_marker'):
        # pytest < 2.4.2 doesn't support get_marker
        rerun_marker = None
        val = item.keywords.get("flaky", None)
        if val is not None:
            from _pytest.mark import MarkInfo, MarkDecorator
            if isinstance(val, (MarkDecorator, MarkInfo)):
                rerun_marker = val
    else:
        #In pytest 2.4.2, we can do this pretty easily.
        rerun_marker = item.get_marker("flaky")

    #Use the marker as a priority over the global setting.
    if rerun_marker is not None:
        fixture_once = False
        pause = 0
        if "reruns" in rerun_marker.kwargs:
            #Check for keyword arguments
            reruns = rerun_marker.kwargs["reruns"]
            if "fixture_once" in rerun_marker.kwargs:
                fixture_once = bool(rerun_marker.kwargs["fixture_once"])
            if "pause" in rerun_marker.kwargs:
                pause = rerun_marker.kwargs["pause"]
        elif len(rerun_marker.args) > 0:
            #Check for arguments
            reruns = rerun_marker.args[0]
            if len(rerun_marker.args) > 1:
                fixture_once = bool(rerun_marker.args[1])
            if len(rerun_marker.args) > 2:
                pause = rerun_marker.args[2]
    elif item.session.config.option.reruns is not None:
        #Default to the global setting
        reruns = item.session.config.option.reruns
    else:
        #Global setting is not specified, and this test is not marked with flaky
        return
    
    # while this doesn't need to be run with every item, it will fail on the first 
    # item if necessary
    check_options(item.session.config)

    item.ihook.pytest_runtest_logstart(
        nodeid=item.nodeid, location=item.location,
    )

    if fixture_once:
        reports, i = repeat_test_only(
            item, nextitem, reruns=reruns, pause=pause, log=False)
    else:
        reports, i = repeat_run(item, nextitem, reruns=reruns, log=False)

    for report in reports:
        if report.when in ("call"):
            if i > 0:
                report.rerun = i
        item.ihook.pytest_runtest_logreport(report=report)

    # pytest_runtest_protocol returns True
    return True


def repeat_run(item, nextitem, reruns=0, log=False):
    """
    Repeat setup, test and teardown
    """
    for i in range(reruns+1):  # ensure at least one run of each item
        reports = runtestprotocol(item, nextitem=nextitem, log=log)
        # break if setup and call pass
        if reports[0].passed and reports[1].passed:
            break

        # break if test marked xfail
        evalxfail = getattr(item, '_evalxfail', None)
        if evalxfail:
            break

    return reports, i

def repeat_test_only(item, nextitem, reruns=0, pause=0, log=False):
    """
    Only repeat the test, just one setup and teardown. Wait for
    pause milliseconds between each run.
    """
    hasrequest = hasattr(item, "_request")
    if hasrequest and not item._request:
        item._initrequest()
    reports = []
    reports.append(call_and_report(item, "setup", log=log))
    if reports[0].passed:
        for i in range(reruns+1):  # ensure at least one run of each item
            rep = call_and_report(item, "call", log=log)

            # break if setup and call pass
            if rep.passed:
                reports.append(rep)
                break

            # break if test marked xfail
            evalxfail = getattr(item, '_evalxfail', None)
            if evalxfail:
                reports.append(rep)
                break

            # pause if necessary
            if pause > 0:
                time.sleep(pause/1000.0)

    if len(reports) == 1:
        reports.append(rep)
    reports.append(call_and_report(item, "teardown", log=log,
        nextitem=nextitem))
    # after all teardown hooks have been called
    # want funcargs and request info to go away
    if hasrequest:
        item._request = False
        item.funcargs = None
    return reports, i

def pytest_report_teststatus(report):
    """ adapted from
    https://bitbucket.org/hpk42/pytest/src/a5e7a5fa3c7e/_pytest/skipping.py#cl-170
    """
    if report.when in ("call"):
        if hasattr(report, "rerun") and report.rerun > 0:
            if report.outcome == "failed":
                return "failed", "F", "FAILED"
            if report.outcome == "passed":
                return "rerun", "R", "RERUN"


def pytest_terminal_summary(terminalreporter):
    """ adapted from
    https://bitbucket.org/hpk42/pytest/src/a5e7a5fa3c7e/_pytest/skipping.py#cl-179
    """
    tr = terminalreporter
    if not tr.reportchars:
        return

    lines = []
    for char in tr.reportchars:
        if char in "rR":
            show_rerun(terminalreporter, lines)

    if lines:
        tr._tw.sep("=", "rerun test summary info")
        for line in lines:
            tr._tw.line(line)


def show_rerun(terminalreporter, lines):
    rerun = terminalreporter.stats.get("rerun")
    if rerun:
        for rep in rerun:
            pos = rep.nodeid
            lines.append("RERUN %s" % (pos,))
