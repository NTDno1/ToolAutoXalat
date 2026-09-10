import type { Category } from './types'


export type ItemMeta = {
  name: string
  icon: string
  category: Category
  payout: number
}

export const ITEM_META: Record<string, ItemMeta> = {
  CA_ROT: { name: 'Cà rốt', icon: '🥕', category: 'VEGETABLE', payout: 5 },
  NGO: { name: 'Ngô', icon: '🌽', category: 'VEGETABLE', payout: 5 },
  CAI: { name: 'Cải', icon: '🥬', category: 'VEGETABLE', payout: 5 },
  CA_CHUA: { name: 'Cà chua', icon: '🍅', category: 'VEGETABLE', payout: 5 },
  BANH_MI: { name: 'Bánh mì', icon: '🌭', category: 'MEAT', payout: 10 },
  XIEN: { name: 'Xiên', icon: '🍢', category: 'MEAT', payout: 15 },
  DUI: { name: 'Đùi', icon: '🍗', category: 'MEAT', payout: 25 },
  BO: { name: 'Bò', icon: '🥩', category: 'MEAT', payout: 45 },
  PIZZA: { name: 'Nổ Pizza', icon: '🍕', category: 'SPECIAL', payout: 20 },
  SALAD: { name: 'Nổ Xà lách', icon: '🥗', category: 'SPECIAL', payout: 20 },
}

export const ITEM_ORDER = [
  'CA_ROT', 'NGO', 'CAI', 'CA_CHUA',
  'BANH_MI', 'XIEN', 'DUI', 'BO', 'PIZZA', 'SALAD',
]
