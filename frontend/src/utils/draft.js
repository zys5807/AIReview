import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * 通用编辑草稿缓存（V1.008 引入，V1.009.10 修正「保存后草稿复活」）
 *
 * 设计意图：**正在编辑、但还没落库的内容不能丢**（切日期 / 误关弹窗都能恢复）。
 *
 * ⚠️ V1.009.10 修掉两个缺陷，表现都是「已保存，却仍提示存在未保存草稿」：
 *
 *  ① 依赖用了对象引用 → effect 每次 render 都重跑。
 *     旧调用写法 `useDraft(key, { formData: manual ? { manual } : null })`：对象字面量
 *     每次 render 都是新引用，`[key, formData]` 的引用比较永不相同 →
 *     每次 render 都重排 400ms 定时器；定时器里 `saveDraft` + `setDraft(新对象)`
 *     又触发 render → 变成 400ms 一个循环。
 *     后果：保存成功后 `clear()` 刚清掉，下一轮就被自动写回来。
 *     → 改为用「序列化字符串」做依赖（内容不变则引用不变）。
 *
 *  ② 从库里读回的内容被当成「新草稿」。
 *     打开某交易日 → 接口把 manual_content 回填进表单 → formData 变化 →
 *     自动存了一份与库里**完全一样**的草稿。用户切走再切回，就弹「检测到未保存的草稿」。
 *     → 引入「基线」（= 该 key 当前已持久化的内容指纹）：
 *        · `markClean(values, key)` 从库里载入后登记基线 → 相同内容不再产生草稿
 *        · `markSaved(values, key)` 保存成功后登记基线 **并清掉草稿**
 *        · 内容与基线相同时不写草稿；`checkDraft` 读到时若与基线相同，当作「多余草稿」清掉
 *
 * ⚠️ **`markClean` 刻意不删 localStorage 里的草稿。**
 *    它会在「从库里载入内容」时被调用，而那份内容随时可能覆盖掉用户尚未确认的编辑；
 *    如果顺手把草稿删了，就会出现「切走再切回，弹窗还没点，草稿已经被清掉」的丢数据问题。
 *    真正的清理只发生在三处：`markSaved`（已落库）、`clear`（用户放弃）、
 *    `checkDraft` 判定「草稿内容与已落库内容一模一样」时。
 *
 * 收紧后的语义：**草稿存在 ⇔ 当前内容 ≠ 已持久化内容**。
 *
 * 安全说明：密码、API Key 等敏感字段【不】进入草稿缓存
 * （ChangePasswordModal / ApiSettingsModal 未接入），避免明文落 localStorage。
 */

const PREFIX = 'airdraft:'

export function getDraft(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}

export function saveDraft(key, data) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify({ ...data, savedAt: Date.now() }))
  } catch (e) {
    /* 隐私模式/存储满时静默失败，不阻塞输入 */
  }
}

export function clearDraft(key) {
  try {
    localStorage.removeItem(PREFIX + key)
  } catch (e) {
    /* ignore */
  }
}

/**
 * React hook：草稿的自动保存 / 恢复 / 清理 / 基线登记
 *
 * @param {string} key      草稿唯一键（同一弹窗 + 同一编辑对象复用同一 key，如
 *                          `daily_review:2026-09-16` / `trade_edit:12`）
 * @param {object} [opts]
 *  - formData: 需要自动保存的表单数据（每次输入变化传入新对象即可，内部 400ms 防抖）
 *  - onDraft:  打开弹窗时若存在草稿，回调该草稿（用于恢复表单）
 * @returns {{ draft, hasDraft, draftTime, checkDraft, markClean, markSaved, clear, save }}
 *
 * 调用契约（V1.009.10 起）：
 *  - 从接口/库里载入一份「已持久化」的内容 → `markClean(values[, key])`（不删草稿）
 *  - 保存成功                             → `markSaved(values[, key])`（登记基线 + 清草稿）
 *  - 用户主动「放弃草稿」（随后内容回到库里的值）→ `clear()`（只清草稿）
 */
export function useDraft(key, { formData, onDraft } = {}) {
  const [draft, setDraft] = useState(null)
  const timerRef = useRef(null)
  const keyRef = useRef(key)
  keyRef.current = key
  const onDraftRef = useRef(onDraft)
  onDraftRef.current = onDraft

  // 序列化后的表单内容：既当 effect 依赖（内容不变 → 引用不变），也当草稿指纹
  const serialized = formData == null ? null : JSON.stringify(formData)
  const serializedRef = useRef(serialized)
  serializedRef.current = serialized

  // 基线：key -> 「已持久化」内容指纹（保存成功 / 从库里读回时登记）
  const baselineRef = useRef({})
  // 上一个 key：切 key 后 formData 仍属于旧 key，这一轮必须丢弃，否则草稿会串日期
  const prevKeyRef = useRef(key)

  useEffect(() => {
    const keyChanged = prevKeyRef.current !== key
    prevKeyRef.current = key
    if (!key || serialized == null || serialized === '{}') return
    // ① 内容已回到「已持久化」状态 → 不产生草稿（但**不删**已有草稿，见文件头说明）
    if (baselineRef.current[key] === serialized) return
    // ② 刚切 key，serialized 还是上一个 key 的内容 → 丢弃，等属于新 key 的内容到位
    if (keyChanged) return

    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      const cur = getDraft(keyRef.current)
      // ③ 草稿已是这份内容 → 不重复写、也不重复 setState（setState 会再触发 render）
      if (cur && JSON.stringify(cur.values) === serialized) return
      saveDraft(keyRef.current, { values: JSON.parse(serialized) })
      setDraft(getDraft(keyRef.current))
    }, 400)
    return () => clearTimeout(timerRef.current)
  }, [key, serialized])

  /**
   * 打开弹窗时调用：检测草稿并自动恢复（可传显式 key，避免 setState 异步导致 key 未更新）
   * 与基线相同的草稿视为「多余」（内容已在库里）→ 清掉并返回 null。
   */
  const checkDraft = useCallback((key) => {
    const k = key ?? keyRef.current
    if (!k) return null
    const d = getDraft(k)
    if (d && baselineRef.current[k] !== undefined && JSON.stringify(d.values) === baselineRef.current[k]) {
      clearDraft(k)
      setDraft(null)
      return null
    }
    setDraft(d)
    if (d && onDraftRef.current) onDraftRef.current(d)
    return d
  }, [])

  /**
   * 登记「基线」：声明这份内容已经持久化 —— 之后内容与它相同就不再产生草稿。
   * **不触碰 localStorage 里已有的草稿**（调用点可能是「从库载入」，草稿仍需保留）。
   * @param {*} [values] 内容（结构必须与传入 hook 的 formData 一致）；省略则用当前内容
   * @param {string} [key] 显式 key（切日期/切编辑对象时 setState 还没生效，必须显式传）
   */
  const markClean = useCallback((values, key) => {
    const k = key ?? keyRef.current
    if (!k) return
    baselineRef.current[k] = values === undefined ? serializedRef.current : JSON.stringify(values)
  }, [])

  /**
   * 保存成功：登记基线 **并清掉草稿**（内容已在库里，草稿再没有存在价值）。
   * 只用 `clear()` 是不够的 —— 内容与基线不同，400ms 后会被自动写回，
   * 页面就一直挂着「有草稿」Tag，切日期还会弹「检测到未保存的草稿」。
   */
  const markSaved = useCallback((values, key) => {
    const k = key ?? keyRef.current
    if (!k) return
    baselineRef.current[k] = values === undefined ? serializedRef.current : JSON.stringify(values)
    clearDraft(k)
    setDraft(null)
  }, [])

  // 只清草稿，**不动基线**（用户主动放弃草稿，随后内容一般会回到库里的值）
  const clear = useCallback((key) => {
    const k = key ?? keyRef.current
    if (!k) return
    clearDraft(k)
    setDraft(null)
  }, [])

  // 立即保存（不常用，防抖已覆盖）
  const save = useCallback(() => {
    if (!keyRef.current || serializedRef.current == null) return
    saveDraft(keyRef.current, { values: JSON.parse(serializedRef.current) })
    setDraft(getDraft(keyRef.current))
  }, [])

  useEffect(() => () => clearTimeout(timerRef.current), [])

  return {
    draft,
    hasDraft: !!draft,
    draftTime: draft?.savedAt ? new Date(draft.savedAt) : null,
    checkDraft,
    markClean,
    markSaved,
    clear,
    save,
  }
}
