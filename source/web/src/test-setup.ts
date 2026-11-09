import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import productProfile from '../../products/groundwater/ui/product-ui.json';
import { decodeProductUi, setProductUi } from './product-ui';

setProductUi(decodeProductUi(productProfile));
configure({ asyncUtilTimeout: 5000 });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
