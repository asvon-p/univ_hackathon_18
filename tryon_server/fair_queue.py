"""Bounded round-robin queue: one garment turn per ready user, FIFO within a user."""
from collections import deque
from queue import Queue


class FairJobQueue(Queue):
    def _init(self, maxsize):
        self.owners = deque()
        self.pending = {}
        self.last_owner = None
        self.count = 0

    def _qsize(self):
        return self.count

    def _put(self, job):
        owner = job.owner_id or "legacy"
        if owner not in self.pending:
            self.pending[owner] = deque()
            self.owners.append(owner)
        self.pending[owner].append(job)
        self.count += 1

    def _get(self):
        if len(self.owners) > 1 and self.owners[0] == self.last_owner:
            self.owners.rotate(-1)
        owner = self.owners.popleft()
        job = self.pending[owner].popleft()
        if self.pending[owner]:
            self.owners.append(owner)
        else:
            del self.pending[owner]
        self.last_owner = owner
        self.count -= 1
        return job

    def position(self, job_id):
        return next((i for i, job in enumerate(self.snapshot(), 1) if job.id == job_id), None)

    def snapshot(self):
        with self.mutex:
            owners = deque(self.owners)
            pending = {k: deque(v) for k, v in self.pending.items()}
            previous = self.last_owner
            ordered = []
            while owners:
                if len(owners) > 1 and owners[0] == previous:
                    owners.rotate(-1)
                owner = owners.popleft()
                item = pending[owner].popleft()
                ordered.append(item)
                if pending[owner]:
                    owners.append(owner)
                previous = owner
            return ordered

    def discard(self, job_id):
        """Immediately return queued capacity on cancellation; running work is not in here."""
        with self.mutex:
            for owner, jobs in list(self.pending.items()):
                for job in list(jobs):
                    if job.id == job_id:
                        jobs.remove(job)
                        if not jobs:
                            del self.pending[owner]
                            self.owners.remove(owner)
                        self.count -= 1
                        self.unfinished_tasks -= 1
                        if not self.unfinished_tasks:
                            self.all_tasks_done.notify_all()
                        self.not_full.notify()
                        return True
        return False
