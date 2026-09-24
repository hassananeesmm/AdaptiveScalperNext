-- 0019_paper_pending_entry: a selected entry signal decided at the last
-- processed bar's close fills at the NEXT bar's open, which may arrive in
-- the next PAPER cycle. Without persisting it the signal was silently
-- dropped at every cycle boundary. (A pending EXIT needs no column: it is
-- part of the open position and travels inside `open_position_json`.)
ALTER TABLE paper_session_state ADD COLUMN pending_entry_json TEXT;
