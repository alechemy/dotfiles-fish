import subprocess
import unittest
from pathlib import Path


SCRIPT = (Path(__file__).parents[2] / "stow/devonthink/Library/Application Scripts"
          / "com.devon-technologies.think/Smart Rules/post-enrich-and-archive.applescript")


class ThingsDeliveryTests(unittest.TestCase):
    def deliver(self, failed_task, previous):
        source = SCRIPT.read_text()
        start = source.index('if oldTasksRaw is missing value or oldTasksRaw is ""')
        end = source.index('add custom meta data updatedTasksRaw for "PreviousTasks"', start)
        body = source[start:end]
        body = body.replace('tell application "Things3"', 'tell me')
        body = body.replace('make new to do with properties {name:taskStr, notes:taskNotes}',
                            'my createTask(taskStr)')
        body = body.replace('log message "Post-Enrich & Archive: Things 3 error: " & thingsErr info recName',
                            'log thingsErr')
        harness = '''property failedTask : ""
on createTask(taskName)
    if (taskName as text) is failedTask then error "fixture failure"
end createTask
on pipelineLog(component, level, msg, recName, recUUID)
    log msg
end pipelineLog
on run argv
    set failedTask to item 1 of argv
    set oldTasksRaw to item 2 of argv
    set theTasks to {"First", "Second", "Third"}
    set recName to "Fictional note"
    set recUUID to "FIXTURE"
    set docLink to "x-devonthink-item://FIXTURE"
''' + body + '\nreturn updatedTasksRaw\nend run'
        result = subprocess.run(["/usr/bin/osascript", "-", failed_task, previous],
                                input=harness, capture_output=True, text=True,
                                check=True, timeout=15)
        return result.stdout.strip().splitlines()

    def test_first_and_later_failure_remain_retryable(self):
        for failed in ("First", "Second"):
            with self.subTest(failed=failed):
                confirmed = self.deliver(failed, "Already")
                self.assertEqual(confirmed, ["Already"] + [
                    task for task in ("First", "Second", "Third") if task != failed])
                retried = self.deliver("", "\r".join(confirmed))
                self.assertEqual(retried, confirmed + [failed])


if __name__ == "__main__":
    unittest.main()
