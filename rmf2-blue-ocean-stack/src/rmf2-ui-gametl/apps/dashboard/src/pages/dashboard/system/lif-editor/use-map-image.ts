import { useEffect, useState } from 'react';
import { BrokerStatusConfig } from '@/clients';

const VDA_BASE_URL = BrokerStatusConfig.BASE ?? 'http://localhost:8000';

export interface MapImageMeta {
  url: string;
  resolution: number;
  origin: [number, number];
  width: number;
  height: number;
}

// Fetches the same master-frame map image metadata used by the VDA visualiser.
export function useMapImage(): MapImageMeta | null {
  const [meta, setMeta] = useState<MapImageMeta | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${VDA_BASE_URL}/map/image/meta`)
      .then((res) =>
        res.ok
          ? (res.json() as Promise<{
              url?: string;
              resolution: number;
              origin: [number, number];
              width: number;
              height: number;
            }>)
          : null,
      )
      .then((data) => {
        if (cancelled || !data) return;
        setMeta({
          url: `${VDA_BASE_URL}${data.url ?? '/map/image'}`,
          resolution: data.resolution,
          origin: data.origin,
          width: data.width,
          height: data.height,
        });
      })
      .catch(() => {
        /* map server not available — skip backdrop */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return meta;
}
