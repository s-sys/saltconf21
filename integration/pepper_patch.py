from pepper.libpepper import Pepper as PepperBase


class Pepper(PepperBase):
    def local(self, tgt, fun, arg=None, kwarg=None, tgt_type='glob', timeout=None, ret=None):
        low = {
            'client': 'local',
            'tgt': tgt,
            'fun': fun,
        }
        if arg:
            low['arg'] = arg
        if kwarg:
            low['kwarg'] = kwarg
        if tgt_type:
            low['tgt_type'] = tgt_type
        if timeout:
            low['timeout'] = timeout
        if ret:
            low['ret'] = ret
        return self.low([low])

    def local_async(self, tgt, fun, arg=None, kwarg=None, tgt_type='glob', timeout=None, ret=None):
        low = {
            'client': 'local_async',
            'tgt': tgt,
            'fun': fun,
        }
        if arg:
            low['arg'] = arg
        if kwarg:
            low['kwarg'] = kwarg
        if tgt_type:
            low['tgt_type'] = tgt_type
        if timeout:
            low['timeout'] = timeout
        if ret:
            low['ret'] = ret
        return self.low([low])

    def local_batch(self, tgt, fun, arg=None, kwarg=None, tgt_type='glob', batch='50%', ret=None):
        low = {
            'client': 'local_batch',
            'tgt': tgt,
            'fun': fun,
        }
        if arg:
            low['arg'] = arg
        if kwarg:
            low['kwarg'] = kwarg
        if tgt_type:
            low['tgt_type'] = tgt_type
        if batch:
            low['batch'] = batch
        if ret:
            low['ret'] = ret
        return self.low([low])
