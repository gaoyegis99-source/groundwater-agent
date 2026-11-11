! Conservative implicit algebraic flux correction, local implementation v1.
! H=-A is the assembled full-tensor high-order system H*c=b.
! L=H+D, where D is a symmetric graph Laplacian chosen to remove positive
! off-diagonals. Raw antidiffusive fluxes D_ij*(cH_i-cH_j) are paired.
! The donor budget limiter ensures b+f_limited >= 0; an M-matrix solve then
! gives a nonnegative concentration. Internal pair fluxes cancel exactly.
! Reservoir-edge corrections are included in the constant-concentration
! mass budget. No post-solve concentration clipping is performed.
! This RHS-budget limiter is deliberately more restrictive than classical
! FCT local-extremum limiters; accuracy and convergence require validation.
module afc_state
  implicit none
  real(8) :: boundary_correction = 0d0
end module

subroutine afc_apply(icomp, dt, time)
  use mt3dms_module, only: nodes,ncrs,l,a,rhs,cnew,icbund,ncol,nrow,nlay,inocross,ncomp,mixelm,iunittrnop
  use afc_state
  use, intrinsic :: ieee_arithmetic, only: ieee_is_finite
  implicit none
  integer,intent(in)::icomp
  real,intent(in)::dt,time
  integer :: nd,i,j,k,rev,edge,ne,iteration,u,limited,donor,head,tail,current,nbr(6),qnodes(nodes),seen(nodes)
  integer,parameter :: reverse(19)=[1,3,2,5,4,7,6,15,14,13,12,11,10,9,8,19,18,17,16]
  real(8),allocatable :: h(:,:),lo(:,:),b(:),outgoing(:),ratio(:),x(:),high(:),correction(:),visc(:),flux(:)
  integer,allocatable :: flag(:),ei(:),ej(:)
  real(8) :: d,f,alpha,val,change,resid,scale,masserr,rawmin,roundoff
  logical :: exists
  boundary_correction=0d0
  if(dt<=0.) return
  if(inocross/=0) error stop 'AFC_REQUIRES_FULL_TENSOR'
  if(ncomp/=1.or.mixelm/=-1) error stop 'AFC_REQUIRES_SINGLE_SPECIES_TVD'
  if(any(iunittrnop([4,7,13,18,19,20])>0)) error stop 'AFC_UNSUPPORTED_REACTION_OR_COUPLED_PACKAGE'
  nd=7
  if(ncrs>0) nd=19
  allocate(h(nodes,nd),lo(nodes,nd),b(nodes),outgoing(nodes),ratio(nodes),x(nodes), &
    high(nodes),correction(nodes),flag(nodes),ei(nodes*nd),ej(nodes*nd),visc(nodes*nd),flux(nodes*nd))
  h=-reshape(real(a,8),[nodes,nd]);lo=h;b=-real(rhs,8)
  high=reshape(real(cnew(:,:,:,icomp),8),[nodes]);flag=reshape(icbund(:,:,:,icomp),[nodes])
  if(any(.not.ieee_is_finite(h)).or.any(.not.ieee_is_finite(b)).or. &
      any(.not.ieee_is_finite(high).and.flag/=0)) error stop 'AFC_NONFINITE_INPUT'
  rawmin=minval(high,mask=flag>0)
  roundoff=0d0
  do i=1,nodes
    if(flag(i)<=0.or.b(i)>=0d0) cycle
    if(-b(i)>1d-12*max(1d0,maxval(abs(b)))) error stop 'AFC_NEGATIVE_INPUT_MASS'
    donor=0;seen=0;head=1;tail=1;qnodes(1)=i;seen(i)=1
    do while(head<=tail.and.donor==0)
      current=qnodes(head);head=head+1
      nbr=[current-1,current+1,current-ncol,current+ncol,current-ncol*nrow,current+ncol*nrow]
      if(mod(current-1,ncol)==0) nbr(1)=0
      if(mod(current,ncol)==0) nbr(2)=0
      if(mod((current-1)/ncol,nrow)==0) nbr(3)=0
      if(mod((current-1)/ncol,nrow)==nrow-1) nbr(4)=0
      do k=1,6
        j=nbr(k)
        if(j<1.or.j>nodes) cycle
        if(flag(j)<=0.or.seen(j)/=0) cycle
        seen(j)=1
        if(b(j)>=-b(i)) then
          donor=j;exit
        endif
        tail=tail+1;qnodes(tail)=j
      enddo
    enddo
    if(donor==0) error stop 'AFC_ROUNDOFF_NO_LOCAL_DONOR'
    f=-b(i)
    b(donor)=b(donor)-f;b(i)=b(i)+f
    roundoff=roundoff+f*dt
  enddo
  ne=0
  do k=2,nd
    rev=reverse(k)
    do i=1,nodes
      j=i+l(k)
      if(j<=i.or.j>nodes) cycle
      if(flag(i)==0.or.flag(j)==0) cycle
      if(flag(i)<0.and.flag(j)<0) cycle
      d=max(0d0,h(i,k),h(j,rev))
      if(d==0d0) cycle
      ne=ne+1;ei(ne)=i;ej(ne)=j;visc(ne)=d
      if(flag(i)>0) then
        lo(i,1)=lo(i,1)+d;lo(i,k)=lo(i,k)-d
      endif
      if(flag(j)>0) then
        lo(j,1)=lo(j,1)+d;lo(j,rev)=lo(j,rev)-d
      endif
    enddo
  enddo
  do i=1,nodes
    if(flag(i)<=0) cycle
    if(lo(i,1)<=0d0.or.sum(lo(i,:))<=0d0) error stop 'AFC_LOW_MATRIX_NOT_DIAGONALLY_DOMINANT'
  enddo
  outgoing=0d0
  do edge=1,ne
    i=ei(edge);j=ej(edge)
    f=visc(edge)*(high(i)-high(j));flux(edge)=f
    outgoing(i)=outgoing(i)+max(0d0,-f)
    outgoing(j)=outgoing(j)+max(0d0, f)
  enddo
  ratio=1d0
  do i=1,nodes
    if(flag(i)>0.and.outgoing(i)>b(i)) ratio(i)=(b(i)/outgoing(i))*(1d0-1d-12)
  enddo
  correction=0d0;limited=0
  do edge=1,ne
    i=ei(edge);j=ej(edge);f=flux(edge)
    if(f>=0d0) then
      alpha=ratio(j)
    else
      alpha=ratio(i)
    endif
    if(alpha<1d0) limited=limited+1
    flux(edge)=alpha*f
    correction(i)=correction(i)+flux(edge)
    correction(j)=correction(j)-flux(edge)
  enddo
  masserr=sum(correction)
  do i=1,nodes
    if(flag(i)>0) b(i)=b(i)+correction(i)
    if(flag(i)/=0.and.b(i)<0d0) error stop 'AFC_LIMITER_RHS_NEGATIVE'
  enddo
  x=0d0
  where(flag<0) x=high
  do iteration=1,10000
    change=0d0
    do i=1,nodes
      if(flag(i)<=0) cycle
      val=b(i)
      do k=2,nd
        j=i+l(k)
        if(j<1.or.j>nodes) cycle
        if(flag(j)==0) cycle
        if(lo(i,k)>0d0) error stop 'AFC_LOW_MATRIX_NOT_MONOTONE'
        val=val-lo(i,k)*x(j)
      enddo
      val=val/lo(i,1)
      change=max(change,abs(val-x(i)));x(i)=val
    enddo
    if(change<1d-13*max(1d0,maxval(x))) exit
  enddo
  if(iteration>10000) error stop 'AFC_LOW_SOLVE_NOT_CONVERGED'
  resid=0d0;scale=max(1d0,maxval(abs(b)))
  do i=1,nodes
    if(flag(i)<=0) cycle
    val=lo(i,1)*x(i)-b(i)
    do k=2,nd
      j=i+l(k)
      if(j<1.or.j>nodes) cycle
      if(flag(j)==0) cycle
      val=val+lo(i,k)*x(j)
    enddo
    resid=max(resid,abs(val)/scale)
  enddo
  if(resid>1d-10.or.any(x<0d0.and.flag/=0).or.any(.not.ieee_is_finite(x))) error stop 'AFC_SOLVE_INVALID'
  boundary_correction=0d0
  do edge=1,ne
    i=ei(edge);j=ej(edge)
    f=-visc(edge)*(x(i)-x(j))+flux(edge)
    if(flag(i)>0.and.flag(j)<0) boundary_correction=boundary_correction+f*real(dt,8)
    if(flag(j)>0.and.flag(i)<0) boundary_correction=boundary_correction-f*real(dt,8)
  enddo
  where(icbund(:,:,:,icomp)>0) cnew(:,:,:,icomp)=reshape(real(x),[ncol,nrow,nlay])
  inquire(file='afc-audit.csv',exist=exists)
  open(newunit=u,file='afc-audit.csv',position='append')
  if(.not.exists) write(u,'(a)') 'time,minimum_predictor,minimum_corrected,pairs,limited_pairs,pair_mass_residual,boundary_mass_correction,linear_residual,roundoff_mass_transfer,pair_mass_scale'
  write(u,'(es24.16,a,es24.16,a,es24.16,a,i0,a,i0,a,es24.16,a,es24.16,a,es24.16,a,es24.16,a,es24.16)') &
    time,',',rawmin,',',minval(x,mask=flag>0),',',ne,',',limited,',',masserr*dt,',',boundary_correction,',',resid,',',roundoff,',',sum(abs(flux(:ne)))*dt
  close(u)
end subroutine

subroutine afc_budget(icomp)
  use mt3dms_module, only:rmasio
  use afc_state
  implicit none
  integer,intent(in)::icomp
  if(boundary_correction>=0d0) then
    rmasio(6,1,icomp)=rmasio(6,1,icomp)+real(boundary_correction)
  else
    rmasio(6,2,icomp)=rmasio(6,2,icomp)+real(boundary_correction)
  endif
end subroutine
