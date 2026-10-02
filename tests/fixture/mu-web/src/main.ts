import { defineConfig } from 'vite';
import assert from 'node:assert';
import { create } from 'zustand';
import { io } from 'socket.io-client';

import { helper } from 'gamma-lib';

const d = `${DESCRIPTION}`;
const s = process.env.SHARED_SECRET;
const rows = supabase.from('audit_log');
export default defineConfig({});
