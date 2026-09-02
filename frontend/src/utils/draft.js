import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * 通用弹窗草稿缓存（V1.008）
 *
 * 痛点：弹窗里写了很多内容，不小心点击其他位置导致弹窗关闭，内容全部丢失。
 * 方案：写内容时防抖自动保存到 localStorage；弹窗（无论何种方式）关闭后内容不丢；
 *       下次打开同一弹窗/同一编辑对象时自动恢复，并显示"已恢复草稿"提示条。
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
 * React hook：弹窗草稿的自动保存 / 恢复 / 清空
 *
 * @param {string} key      草稿唯一键（同一弹窗 + 同一编辑对象复用同一 key，如 `trade_edit:12` / `phase_summary:week:2026-08-31~2026-09-06:all`）
 * @param {object} [opts]
 *  - formData: 需要自动保存的表单数据（每次输入变化传入新对象即可，内部 400ms 防抖）
 *  - onDraft:  打开弹窗时若存在草稿，回调该草稿（用于恢复表单）
 * @returns {{ draft, hasDraft, draftTime, checkDraft, clear, save }}
 */
export function useDraft(key, { formData, onDraft } = {}) {
  const [draft, setDraft] = useState(null)
  const timerRef = useRef(null)
  const keyRef = useRef(key)
  keyRef.current = key
  const onDraftRef = useRef(onDraft)
  onDraftRef.current = onDraft

  // 输入变化 → 防抖自动保存
  useEffect(() => {
    if (!key || formData == null) return
    if (JSON.stringify(formData) === '{}') return
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      saveDraft(keyRef.current, { values: formData })
      setDraft(getDraft(keyRef.current))
    }, 400)
    return () => clearTimeout(timerRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, formData])

  // 打开弹窗时调用：检测草稿并自动恢复（可传显式 key，避免 setState 异步导致 key 未更新）
  const checkDraft = useCallback((key) => {
    const k = key ?? keyRef.current
    if (!k) return null
    const d = getDraft(k)
    setDraft(d)
    if (d && onDraftRef.current) onDraftRef.current(d)
    return d
  }, [])

  // 清空草稿（保存成功 / 用户主动放弃）
  const clear = useCallback(() => {
    clearDraft(keyRef.current)
    setDraft(null)
  }, [])

  // 立即保存（不常用，防抖已覆盖）
  const save = useCallback(() => {
    if (!keyRef.current || formData == null) return
    saveDraft(keyRef.current, { values: formData })
    setDraft(getDraft(keyRef.current))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData])

  useEffect(() => () => clearTimeout(timerRef.current), [])

  return {
    draft,
    hasDraft: !!draft,
    draftTime: draft?.savedAt ? new Date(draft.savedAt) : null,
    checkDraft,
    clear,
    save,
  }
}
