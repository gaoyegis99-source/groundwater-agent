import { hashFile } from './hash';

self.onmessage = async (event: MessageEvent<File>) => {
  try {
    const checksum = await hashFile(event.data, processed => self.postMessage({ processed }));
    self.postMessage({ checksum });
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
    self.postMessage({ error: 'FILE_READ_FAILED' });
  }
};
