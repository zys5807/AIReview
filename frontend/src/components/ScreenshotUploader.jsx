import { useState, useEffect } from 'react'
import { Upload, Button, Select, Image, Space, Empty } from 'antd'
import { InboxOutlined, DeleteOutlined } from '@ant-design/icons'
import { uploadScreenshot } from '../api'
import { API_BASE } from '../api/client'

const { Dragger } = Upload

export const SCREENSHOT_ROLES = ['背景1', '背景2', '次级别1', '后续', '其他']

const shotUrl = (shot) =>
  shot ? `${API_BASE}/uploads/${shot.stored_path.replace('uploads/', '')}` : null

/**
 * 多截图上传器：支持上传多张、每张指定角色、删除
 * @param initial [{screenshot_id, role, stored_path}] 已有关联
 * @param onChange (list) => void  list: [{key, screenshot_id, role}]
 */
export default function ScreenshotUploader({ initial = [], onChange }) {
  const [shots, setShots] = useState([])
  const [uploading, setUploading] = useState(false)

  // 仅当 initial 数量变化时同步内部列表。
  // 注意：不能用 [initial] 作依赖——父组件每次渲染都传新的数组字面量，会反复清空已上传的截图。
  useEffect(() => {
    setShots(
      initial.map((s, i) => ({
        key: `init-${i}`,
        screenshot_id: s.screenshot_id,
        role: s.role || '后续',
        url: s.stored_path ? shotUrl(s) : null,
      })),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.length])

  const emit = (next) => {
    setShots(next)
    onChange?.(next.map((s) => ({ screenshot_id: s.screenshot_id, role: s.role })))
  }

  const handleUpload = async (file) => {
    setUploading(true)
    try {
      const shot = await uploadScreenshot(file)
      emit([
        ...shots,
        {
          key: Date.now(),
          screenshot_id: shot.id,
          role: '后续',
          url: shotUrl(shot),
        },
      ])
    } catch (e) {
      /* 拦截器已提示 */
    } finally {
      setUploading(false)
    }
    return false
  }

  const changeRole = (key, role) =>
    emit(shots.map((s) => (s.key === key ? { ...s, role } : s)))

  const removeShot = (key) => emit(shots.filter((s) => s.key !== key))

  return (
    <div>
      {shots.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="尚未关联截图"
          style={{ marginBottom: 8 }}
        />
      ) : (
        <Space orientation="vertical" size={8} style={{ width: '100%', marginBottom: 8 }}>
          {shots.map((s) => (
            <div
              key={s.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: 6,
                background: '#fafafa',
                borderRadius: 6,
              }}
            >
              {s.url && (
                <Image src={s.url} alt="截图" style={{ maxHeight: 56, maxWidth: 90 }} />
              )}
              <span style={{ fontSize: 12, color: '#888', flex: 1 }}>
                截图#{s.screenshot_id}
              </span>
              <Select
                size="small"
                style={{ width: 110 }}
                value={s.role}
                onChange={(v) => changeRole(s.key, v)}
                options={SCREENSHOT_ROLES.map((r) => ({ value: r, label: r }))}
              />
              <Button
                type="text"
                danger
                size="small"
                icon={<DeleteOutlined />}
                onClick={() => removeShot(s.key)}
              />
            </div>
          ))}
        </Space>
      )}

      <Dragger
        accept=".png,.jpg,.jpeg,.gif,.webp,.bmp"
        showUploadList={false}
        beforeUpload={handleUpload}
        disabled={uploading}
        style={{ padding: '12px 0' }}
      >
        <p className="ant-upload-drag-icon" style={{ marginBottom: 0 }}>
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">点击或拖拽上传截图（可多张，每张指定角色）</p>
        <p className="ant-upload-hint">
          背景1(日线判多空) / 背景2(1小时判方向) / 次级别1(15分钟判入场) / 后续(标注入场离场)
        </p>
      </Dragger>
    </div>
  )
}
