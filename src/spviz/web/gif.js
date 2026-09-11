class SpvizGifEncoder {
  constructor(width,height,delay=9){this.width=width;this.height=height;this.delay=delay;this.parts=[];this._header()}
  _bytes(values){this.parts.push(Uint8Array.from(values))}
  _word(value){this._bytes([value&255,(value>>8)&255])}
  _header(){this._bytes([...new TextEncoder().encode('GIF89a')]);this._word(this.width);this._word(this.height);this._bytes([0xf7,0,0]);const palette=[];for(let r=0;r<8;r++)for(let g=0;g<8;g++)for(let b=0;b<4;b++)palette.push(Math.round(r*255/7),Math.round(g*255/7),Math.round(b*255/3));this._bytes(palette);this._bytes([0x21,0xff,0x0b,...new TextEncoder().encode('NETSCAPE2.0'),3,1,0,0,0])}
  _indices(rgba){const output=new Uint8Array(this.width*this.height);for(let i=0,j=0;i<rgba.length;i+=4,j++)output[j]=((rgba[i]>>5)<<5)|((rgba[i+1]>>5)<<2)|(rgba[i+2]>>6);return output}
  _lzw(indices){const clear=256,end=257,output=[],dictionary=new Map();let next=258,bits=0,count=0;const emit=code=>{bits|=code<<count;count+=9;while(count>=8){output.push(bits&255);bits>>>=8;count-=8}};const reset=()=>{dictionary.clear();next=258};emit(clear);let prefix=indices[0];for(let i=1;i<indices.length;i++){const value=indices[i],key=prefix*256+value,found=dictionary.get(key);if(found!==undefined){prefix=found;continue}emit(prefix);if(next<511)dictionary.set(key,next++);else{emit(clear);reset()}prefix=value}emit(prefix);emit(end);if(count>0)output.push(bits&255);return Uint8Array.from(output)}
  addFrame(rgba){this._bytes([0x21,0xf9,4,4,this.delay&255,(this.delay>>8)&255,0,0,0x2c]);this._word(0);this._word(0);this._word(this.width);this._word(this.height);this._bytes([0,8]);const compressed=this._lzw(this._indices(rgba));for(let offset=0;offset<compressed.length;offset+=255){const block=compressed.subarray(offset,offset+255);this._bytes([block.length]);this.parts.push(block)}this._bytes([0])}
  finish(){this._bytes([0x3b]);return new Blob(this.parts,{type:'image/gif'})}
}
