# Coordinator 運用

1. Codex には全リポジトリ共通の Coordinator を一件だけ保持します。
2. 通常の操作、レビュー返信、コメント、質問、方向修正は Coordinator
   だけに書きます。対象プロジェクト名だけ明示すればよく、通常は
   プロジェクト別の新規タスクを作りません。
3. 対象リポジトリの context がある場合は
   `Use $supervise-repo-loop for this repository until terminal state.` を使います。
4. 登録済みリポジトリを進める場合は
   `Use $supervise-repo-loop for the next actionable registered repository until terminal state.`
   を使います。
5. Web Supervisor と Codex Worker には直接書きません。Coordinator が exact
   bindingを検証し、同じSupervisorと永続Workerを再利用します。
6. ユーザー入力は、まずreceipt ID付きで `RECEIVED` になります。その後、
   `ROUTED` と `ADOPTED / DEFERRED / REJECTED / NEEDS_CLARIFICATION /
   SUPERSEDED` のいずれかまで追跡されます。`RECEIVED` は採用済みという意味では
   ありません。
7. レビュー待ち、質問、方向修正、ブロックは、その正確なMissionまたは
   プロジェクトだけをparkします。他プロジェクトを停止する条件にはなりません。
8. レビューカードは全件完了を待たず、一件ずつ表示されます。`light / standard /
   deep` は確認の深さであり、停止範囲ではありません。
9. 外部依頼を送ったプロジェクトは、delivery tokenとcursorを持つroute leaseとして
   待機します。そのleaseは全体Schedulerを占有しません。空きがあれば別プロジェクトの
   READYを開始します。
10. 同時外部routeは原則最大3件、同じプロジェクトのexecution routeは最大1件です。
    一巡につき各プロジェクトで新しい仕事の開始は最大1つです。ただし、到着結果の取込み
    から次のSupervisor/Worker待ちまでの必須ハンドオフは同じ一巡で完了させます。
    その後round-robinで進めます。
11. 待機は全routeを一度に監視し、最初に変化したrouteだけ処理します。60秒以内に
    変化がなければ、同じ待機実況を繰り返さず、exact wait setを保存してquiet
    checkpointへ移ります。
12. recovery leaseは外部routeが残ったまま前景ターンを終了した期間だけ有効です。
    前景待機との二重稼働や、作業のない定期model wakeは行いません。
13. `READY` は次処理が特定済み、`DRAINING` はclaimまたはroute leaseあり、
    `WAITING_USER` はユーザー入力待ち、`WAITING_EXTERNAL` は外部条件待ち、`IDLE` は
    静かな待機です。`AVAILABLE` はCoordinatorへいつでも書けるという意味で、進行中を
    意味しません。
14. 現Missionの完了とプロジェクト終了は別です。
    `MISSION_COMPLETE_NEXT_UNSELECTED` は次Mission選定前、`PARKED_BY_POLICY` は方針上の
    休止、`PROJECT_COMPLETE` は明示的に終端した場合だけ使います。
15. 状態が変わると、全登録プロジェクトを含む一つの常設Coordinator索引が更新されます。
    実行理由、停止理由、担当、次の一手、解除条件、ユーザー操作の有無、成果物、Worker
    Report、Supervisor判定を同じ索引から確認できます。回答本文にも、Mission→Work Order→
    Worker→Worker Report→Supervisor→Verdict→Next Routeの共通図が表示されます。
16. BLOCKED行には、目的、影響、必要条件、状態、owner、解除条件、ユーザーが可能な操作、
    要求が発生したevent・時刻・証拠、必要になった理由、条件を満たす例／満たさない例、
    実施済み診断、入力先・形式、次に許可されるprobeが表示されます。「条件を満たす資料」
    だけの表示は不正です。変更のない同一失敗は再試行しません。
17. `READY` は空き容量がある同じターンでclaimします。実行・安全上限で残す場合は、
    action ID、owner、期限、再開eventを保存します。ownerのないREADYは正常状態では
    ありません。
18. 現在の全Missionが終端してもCoordinator自体は利用可能です。ただし実作業もrouteも
    ない場合は「進行中」と表示しません。
19. Coordinator索引は意味のある状態変化だけで更新されます。時刻だけの変化、active表示、
    無変更timeout、同じheartbeatは進捗として通知されません。
20. 「全体状況を一覧で表示」など通常の状況確認でも、既に到着しているWorker/Supervisor
    結果は先に取り込み、次の必須ハンドオフまで進めてから表示します。新しい仕事を始めず
    完全な読取専用にしたい場合だけ、その旨を明示します。
21. 実装、push、PR、merge、release、publication、deployment、rights、production、
    human acceptanceは別の権限です。ローカル検証やMission完了から推測しません。
