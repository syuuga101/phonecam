# -*- coding: utf-8 -*-
"""Qt を使わない簡易シグナル (Controller / CameraGroup が使う)。

画面の部品 (ページ) を作っている間は CallbackList.scope にその目印を入れておく。
そのあいだに connect() された受け手には目印が付くので、ページを作り直す (言語の切り替えなど) ときに
drop_scope(目印) で古いページの受け手だけをまとめて外せる (外さないと、消えたページに通知し続ける)。
"""

import weakref


class CallbackList:
    _all = weakref.WeakSet()
    scope = None

    def __init__(self):
        self._cbs = []
        self._scopes = []
        CallbackList._all.add(self)

    def connect(self, fn):
        if fn not in self._cbs:
            self._cbs.append(fn)
            self._scopes.append(CallbackList.scope)
        return fn

    def disconnect(self, fn):
        try:
            index = self._cbs.index(fn)
        except ValueError:
            return
        del self._cbs[index]
        del self._scopes[index]

    def emit(self, *args, **kw):
        for fn in list(self._cbs):
            try:
                fn(*args, **kw)
            except Exception:
                pass

    @classmethod
    def drop_scope(cls, scope):
        """scope の間に connect された受け手をすべて外す。"""
        if scope is None:      # 目印のない受け手 (画面の外が繋いだもの) は外さない
            return
        for signal in list(cls._all):
            keep = [(fn, owner) for fn, owner in zip(signal._cbs, signal._scopes) if owner is not scope]
            signal._cbs = [fn for fn, _ in keep]
            signal._scopes = [owner for _, owner in keep]
