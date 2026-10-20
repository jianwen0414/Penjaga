// The notification bell: a round button in the 3D view's toolbar with a count of what's unread. While something
// new is waiting it keeps ringing, and it only stops once the owner opens the list and sees it.
import { Icon } from './ui';
import '../styles/notify-bell.css';

export default function NotifyBell({ count, tone, ringing, onClick }: {
  /** Unread notifications. */
  count: number;
  tone: 'urgent' | 'act' | 'calm';
  /** Something new the owner hasn't looked at yet. */
  ringing: boolean;
  /** Tap: show every notification, in a list that drops down under the bell. */
  onClick: (bell: HTMLButtonElement) => void;
}) {
  const label = count ? `${count} unread notification${count > 1 ? 's' : ''}. Show all.` : 'Notifications. Show all.';
  return (
    <button className={`round nbell ${ringing ? `ringing ${tone}` : ''}`} onClick={(e) => onClick(e.currentTarget)} aria-label={label}>
      <Icon name="bell" size={18} />
      {count > 0 && <span key={count} className={`nbell-count ${tone === 'urgent' ? '' : tone}`}>{count}</span>}
    </button>
  );
}
